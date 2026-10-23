/* Optional macOS real-GPU smoke test. This contains an original no-op compute
 * shader, never game assets. Link Biko3Vulkan.c and static MoltenVK with Metal,
 * QuartzCore, IOSurface, Foundation and libc++. A green run establishes bridge
 * instance/device/compute/queue/lifecycle behavior, not game rendering. */
#include "../../ios/runtime/Biko3Vulkan.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static const uint32_t compute_shader[] = {
    0x07230203, 0x00010000, 0, 5, 0,
    0x00020011, 1,                     /* OpCapability Shader */
    0x0003000e, 0, 1,                  /* OpMemoryModel Logical GLSL450 */
    0x0005000f, 5, 3, 0x6e69616d, 0,   /* OpEntryPoint GLCompute %3 "main" */
    0x00060010, 3, 17, 1, 1, 1,        /* LocalSize 1 1 1 */
    0x00020013, 1,                     /* %1 = OpTypeVoid */
    0x00030021, 2, 1,                  /* %2 = OpTypeFunction %1 */
    0x00050036, 1, 3, 0, 2,            /* %3 = OpFunction %1 None %2 */
    0x000200f8, 4, 0x000100fd, 0x00010038
};
typedef struct {
    PFN_vkQueueSubmit submit;
    VkQueue queue; VkSubmitInfo info; VkFence fence;
    atomic_int started, completed;
    VkResult result;
} SubmitTask;
static void pause_ms(long ms) {
    struct timespec duration = {ms / 1000, (ms % 1000) * 1000000};
    nanosleep(&duration, NULL);
}
static void *submit_thread(void *argument) {
    SubmitTask *task = argument;
    atomic_store(&task->started, 1);
    task->result = task->submit(task->queue, 1, &task->info, task->fence);
    atomic_store(&task->completed, 1); return NULL;
}
#define REQUIRE(call) do { VkResult result_ = (call); if (result_ != VK_SUCCESS) { \
    fprintf(stderr, "%s: %d\n", #call, result_); goto cleanup; } } while (0)

int main(void) {
    int status = 1;
    VkInstance instance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkShaderModule shader = VK_NULL_HANDLE;
    VkPipelineLayout layout = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    VkPhysicalDevice *physical = NULL;
    VkQueueFamilyProperties *families = NULL;
    PFN_vkCreateInstance create_instance = (PFN_vkCreateInstance)biko3_vk_resolve("vkCreateInstance");
    PFN_vkCreateDevice create_device = (PFN_vkCreateDevice)biko3_vk_resolve("vkCreateDevice");
    PFN_vkDestroyDevice destroy_device = (PFN_vkDestroyDevice)biko3_vk_resolve("vkDestroyDevice");
    PFN_vkQueueSubmit submit = (PFN_vkQueueSubmit)biko3_vk_resolve("vkQueueSubmit");
    VkApplicationInfo application = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "Biko3 bridge compute smoke", .apiVersion = VK_API_VERSION_1_1};
    VkInstanceCreateInfo instance_info = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &application};
    VkResult instance_result = create_instance(&instance_info, NULL, &instance);
    if (instance_result == VK_ERROR_INCOMPATIBLE_DRIVER) {
        fprintf(stderr, "SKIP: Vulkan driver does not support instance creation\n");
        status = 77; goto cleanup;
    }
    REQUIRE(instance_result);
    uint32_t count = 0;
    REQUIRE(vkEnumeratePhysicalDevices(instance, &count, NULL));
    if (!count) { fprintf(stderr, "SKIP: no Vulkan physical device\n"); status = 77; goto cleanup; }
    physical = calloc(count, sizeof(*physical));
    if (!physical) goto cleanup;
    REQUIRE(vkEnumeratePhysicalDevices(instance, &count, physical));
    VkPhysicalDevice selected = VK_NULL_HANDLE;
    uint32_t family = 0;
    for (uint32_t i = 0; i < count && !selected; ++i) {
        uint32_t n = 0; vkGetPhysicalDeviceQueueFamilyProperties(physical[i], &n, NULL);
        families = calloc(n ? n : 1, sizeof(*families));
        if (!families) goto cleanup;
        vkGetPhysicalDeviceQueueFamilyProperties(physical[i], &n, families);
        for (uint32_t j = 0; j < n; ++j) {
            if (families[j].queueCount && (families[j].queueFlags & 3) == 3) {
                selected = physical[i]; family = j; break;
            }
        }
        free(families); families = NULL;
    }
    if (!selected) { fprintf(stderr, "SKIP: no graphics+compute queue\n"); status = 77; goto cleanup; }
    float priority = 1.0f;
    VkDeviceQueueCreateInfo queue_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info};
    REQUIRE(create_device(selected, &device_info, NULL, &device));
    VkQueue queue; vkGetDeviceQueue(device, family, 0, &queue);
    VkShaderModuleCreateInfo shader_info = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = sizeof(compute_shader), .pCode = compute_shader};
    REQUIRE(vkCreateShaderModule(device, &shader_info, NULL, &shader));
    VkPipelineLayoutCreateInfo layout_info = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    REQUIRE(vkCreatePipelineLayout(device, &layout_info, NULL, &layout));
    VkComputePipelineCreateInfo compute_info = {.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
                  .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = shader, .pName = "main"},
        .layout = layout};
    REQUIRE(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &compute_info, NULL, &pipeline));
    VkCommandPoolCreateInfo pool_info = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .queueFamilyIndex = family};
    REQUIRE(vkCreateCommandPool(device, &pool_info, NULL, &pool));
    VkCommandBuffer command;
    VkCommandBufferAllocateInfo command_info = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    REQUIRE(vkAllocateCommandBuffers(device, &command_info, &command));
    VkCommandBufferBeginInfo begin_info = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    REQUIRE(vkBeginCommandBuffer(command, &begin_info));
    vkCmdBindPipeline(command, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdDispatch(command, 1, 1, 1);
    REQUIRE(vkEndCommandBuffer(command));
    VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    REQUIRE(vkCreateFence(device, &fence_info, NULL, &fence));
    SubmitTask task = {.submit = submit, .queue = queue, .fence = fence,
        .info = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &command},
        .started = ATOMIC_VAR_INIT(0), .completed = ATOMIC_VAR_INIT(0)};
    biko3_vk_suspend(1);
    pthread_t thread;
    if (pthread_create(&thread, NULL, submit_thread, &task)) { biko3_vk_suspend(0); goto cleanup; }
    while (!atomic_load(&task.started)) pause_ms(1);
    pause_ms(100);
    int submitted_while_suspended = atomic_load(&task.completed);
    biko3_vk_suspend(0);
    pthread_join(thread, NULL);
    if (submitted_while_suspended) { fprintf(stderr, "Submit bypassed suspension\n"); goto cleanup; }
    REQUIRE(task.result);
    REQUIRE(vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_C(10000000000)));
    biko3_vk_suspend(1); biko3_vk_suspend(0);
    puts("PASS: real Vulkan compute dispatch, fence, suspend/resume");
    status = 0;
cleanup:
    if (device) {
        vkDeviceWaitIdle(device);
        if (fence) vkDestroyFence(device, fence, NULL);
        if (pool) vkDestroyCommandPool(device, pool, NULL);
        if (pipeline) vkDestroyPipeline(device, pipeline, NULL);
        if (layout) vkDestroyPipelineLayout(device, layout, NULL);
        if (shader) vkDestroyShaderModule(device, shader, NULL);
        destroy_device(device, NULL);
    }
    if (instance) vkDestroyInstance(instance, NULL);
    free(physical); free(families); return status;
}
