/* Native macOS probe for ORIGINAL game compute SPIR-V extracted locally.
 * Inputs contain paths + reflected layout metadata, never replacement shaders.
 * This creates pipelines only. It deliberately does not dispatch game kernels
 * because buffer lengths, descriptor contents and invocation data are unproven.
 * game_shader_smoke.py generates game_compute_inputs.h before compilation. */
#include "../../ios/runtime/Biko3Vulkan.h"
#include <CommonCrypto/CommonDigest.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    uint32_t set, binding;
    VkDescriptorType type;
    uint32_t count;
} ComputeBinding;
typedef struct {
    const char *path, *sha256, *role, *entry_point;
    const ComputeBinding *bindings;
    uint32_t binding_count, push_size;
    uint32_t local_size[3];
} ComputeCase;
#include "game_compute_inputs.h"

static int read_shader(const ComputeCase *test, uint32_t **words, size_t *bytes) {
    *words = NULL; *bytes = 0;
    FILE *file = fopen(test->path, "rb");
    if (!file) { fprintf(stderr, "Cannot open original shader: %s\n", test->path); return 0; }
    if (fseek(file, 0, SEEK_END)) { fclose(file); return 0; }
    long length = ftell(file);
    if (length < 20 || length > 16 * 1024 * 1024 || length % 4 || fseek(file, 0, SEEK_SET)) {
        fclose(file); fprintf(stderr, "Invalid original SPIR-V length\n"); return 0;
    }
    uint32_t *data = malloc((size_t)length);
    if (!data) { fclose(file); return 0; }
    size_t read = fread(data, 1, (size_t)length, file);
    int failed = ferror(file); fclose(file);
    if (failed || read != (size_t)length) { free(data); return 0; }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    char hex[CC_SHA256_DIGEST_LENGTH * 2 + 1];
    CC_SHA256(data, (CC_LONG)length, digest);
    for (size_t i = 0; i < sizeof(digest); ++i) snprintf(hex + i * 2, 3, "%02x", digest[i]);
    if (strcmp(hex, test->sha256) || data[0] != 0x07230203) {
        fprintf(stderr, "Original game shader SHA256 mismatch: %s\n", test->role);
        free(data); return 0;
    }
    *words = data; *bytes = (size_t)length; return 1;
}

#define CHECK_PIPELINE(call) do { VkResult result_ = (call); if (result_ != VK_SUCCESS) { \
    fprintf(stderr, "%s %s: %d\n", test->role, #call, result_); goto cleanup; } } while (0)

static int compile_original(VkDevice device, const VkPhysicalDeviceProperties *properties, const ComputeCase *test) {
    int passed = 0;
    uint32_t *words = NULL;
    size_t bytes = 0;
    VkShaderModule shader = VK_NULL_HANDLE;
    VkDescriptorSetLayout descriptors = VK_NULL_HANDLE;
    VkPipelineLayout layout = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkDescriptorSetLayoutBinding *bindings = NULL;
    if (!read_shader(test, &words, &bytes)) goto cleanup;
    uint64_t invocations = (uint64_t)test->local_size[0] * test->local_size[1] * test->local_size[2];
    if (invocations > properties->limits.maxComputeWorkGroupInvocations ||
        test->push_size > properties->limits.maxPushConstantsSize) {
        fprintf(stderr, "%s: actual device compute/push-constant limit is insufficient\n", test->role);
        goto cleanup;
    }
    for (unsigned i = 0; i < 3; ++i) {
        if (test->local_size[i] > properties->limits.maxComputeWorkGroupSize[i]) {
            fprintf(stderr, "%s: actual device workgroup dimension limit is insufficient\n", test->role);
            goto cleanup;
        }
    }
    bindings = calloc(test->binding_count ? test->binding_count : 1, sizeof(*bindings));
    if (!bindings) goto cleanup;
    for (uint32_t i = 0; i < test->binding_count; ++i) {
        const ComputeBinding *binding = &test->bindings[i];
        if (binding->set != 0 || !binding->count ||
            (binding->type != VK_DESCRIPTOR_TYPE_STORAGE_BUFFER && binding->type != VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER)) {
            fprintf(stderr, "%s: unsupported reflected descriptor layout\n", test->role); goto cleanup;
        }
        bindings[i] = (VkDescriptorSetLayoutBinding){.binding = binding->binding,
            .descriptorType = binding->type, .descriptorCount = binding->count,
            .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT};
    }
    VkShaderModuleCreateInfo shader_info = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = bytes, .pCode = words};
    CHECK_PIPELINE(vkCreateShaderModule(device, &shader_info, NULL, &shader));
    VkDescriptorSetLayoutCreateInfo descriptor_info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = test->binding_count, .pBindings = bindings};
    CHECK_PIPELINE(vkCreateDescriptorSetLayout(device, &descriptor_info, NULL, &descriptors));
    VkPushConstantRange push = {.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT, .offset = 0, .size = test->push_size};
    VkPipelineLayoutCreateInfo layout_info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1, .pSetLayouts = &descriptors,
        .pushConstantRangeCount = test->push_size ? 1 : 0,
        .pPushConstantRanges = test->push_size ? &push : NULL};
    CHECK_PIPELINE(vkCreatePipelineLayout(device, &layout_info, NULL, &layout));
    VkComputePipelineCreateInfo pipeline_info = {.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = shader, .pName = test->entry_point},
        .layout = layout};
    CHECK_PIPELINE(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &pipeline_info, NULL, &pipeline));
    if (!pipeline) { fprintf(stderr, "%s: success with NULL pipeline\n", test->role); goto cleanup; }
    /* Names and hashes originate in the two hash-bound fixed game profiles. */
    printf("BIKO3_COMPUTE_RESULT {\"sha256\":\"%s\",\"stage\":\"comp\",\"entry_point\":\"%s\","
           "\"role\":\"%s\",\"pipeline_created\":true,\"kernel_dispatched\":false}\n",
           test->sha256, test->entry_point, test->role);
    fflush(stdout); passed = 1;
cleanup:
    if (pipeline) vkDestroyPipeline(device, pipeline, NULL);
    if (layout) vkDestroyPipelineLayout(device, layout, NULL);
    if (descriptors) vkDestroyDescriptorSetLayout(device, descriptors, NULL);
    if (shader) vkDestroyShaderModule(device, shader, NULL);
    free(bindings); free(words); return passed;
}

int main(void) {
    int status = 1;
    VkInstance instance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkPhysicalDevice *physical = NULL;
    VkQueueFamilyProperties *families = NULL;
    PFN_vkCreateInstance create_instance = (PFN_vkCreateInstance)biko3_vk_resolve("vkCreateInstance");
    PFN_vkCreateDevice create_device = (PFN_vkCreateDevice)biko3_vk_resolve("vkCreateDevice");
    PFN_vkDestroyDevice destroy_device = (PFN_vkDestroyDevice)biko3_vk_resolve("vkDestroyDevice");
    if (!create_instance || !create_device || !destroy_device || sizeof(compute_cases) / sizeof(compute_cases[0]) != 2)
        goto cleanup;
    VkApplicationInfo application = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "Biko3 original compute pipeline probe", .apiVersion = VK_API_VERSION_1_1};
    VkInstanceCreateInfo instance_info = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &application};
    VkResult result = create_instance(&instance_info, NULL, &instance);
    if (result == VK_ERROR_INCOMPATIBLE_DRIVER) {
        puts("BIKO3_COMPUTE_SKIP incompatible_vulkan_driver"); status = 77; goto cleanup;
    }
    if (result != VK_SUCCESS) { fprintf(stderr, "CreateInstance failed: %d\n", result); goto cleanup; }
    uint32_t count = 0;
    result = vkEnumeratePhysicalDevices(instance, &count, NULL);
    if (result != VK_SUCCESS) { fprintf(stderr, "EnumeratePhysicalDevices failed: %d\n", result); goto cleanup; }
    if (!count) { puts("BIKO3_COMPUTE_SKIP zero_physical_devices"); status = 77; goto cleanup; }
    uint32_t capacity = count;
    physical = calloc(capacity, sizeof(*physical));
    if (!physical) goto cleanup;
    result = vkEnumeratePhysicalDevices(instance, &count, physical);
    if (result != VK_SUCCESS || count > capacity) {
        fprintf(stderr, "Physical device snapshot failed: %d\n", result); goto cleanup;
    }
    VkPhysicalDevice selected = VK_NULL_HANDLE;
    uint32_t family = 0;
    for (uint32_t i = 0; i < count && !selected; ++i) {
        uint32_t n = 0; vkGetPhysicalDeviceQueueFamilyProperties(physical[i], &n, NULL);
        uint32_t queue_capacity = n;
        families = calloc(n ? n : 1, sizeof(*families));
        if (!families) goto cleanup;
        vkGetPhysicalDeviceQueueFamilyProperties(physical[i], &n, families);
        if (n > queue_capacity) { fprintf(stderr, "Queue family count changed\n"); goto cleanup; }
        for (uint32_t j = 0; j < n; ++j) {
            if (families[j].queueCount &&
                (families[j].queueFlags & (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT)) ==
                (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT)) {
                selected = physical[i]; family = j; break;
            }
        }
        free(families); families = NULL;
    }
    if (!selected) { puts("BIKO3_COMPUTE_SKIP no_graphics_compute_queue"); status = 77; goto cleanup; }
    VkPhysicalDeviceProperties properties; vkGetPhysicalDeviceProperties(selected, &properties);
    fprintf(stderr, "Actual Vulkan device: %s (vendor %u device %u)\n", properties.deviceName, properties.vendorID, properties.deviceID);
    float priority = 1.0f;
    VkDeviceQueueCreateInfo queue_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info};
    result = create_device(selected, &device_info, NULL, &device);
    if (result != VK_SUCCESS) { fprintf(stderr, "CreateDevice failed: %d\n", result); goto cleanup; }
    for (size_t i = 0; i < sizeof(compute_cases) / sizeof(compute_cases[0]); ++i) {
        if (!compile_original(device, &properties, &compute_cases[i])) goto cleanup;
    }
    status = 0;
cleanup:
    if (device) destroy_device(device, NULL);
    if (instance) vkDestroyInstance(instance, NULL);
    free(families); free(physical); return status;
}
