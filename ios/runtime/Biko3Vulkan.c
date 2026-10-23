#include "Biko3Vulkan.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
#include <pthread.h>

/* NN is a guest platform extension; its sole window field is deliberately never
 * dereferenced. All non-platform Vulkan handles remain real MoltenVK handles. */
#define NN_SURFACE "VK_NN_vi_surface"
typedef struct { VkStructureType sType; const void *pNext; VkFlags flags; void *window; } GuestViSurfaceInfo;
static _Atomic(const void *) metal_layer;
void biko3_vk_set_metal_layer(const void *layer) { atomic_store(&metal_layer, layer); }

/* There is one renderer/device in this game's analyzed create path. The state
 * lock never crosses Vulkan calls; lifecycle serializes idle/destruction. */
static pthread_mutex_t graphics_state = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t graphics_lifecycle = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t graphics_changed = PTHREAD_COND_INITIALIZER;
static VkDevice active_device;
static unsigned active_queue_calls;
static int graphics_suspended;
static int device_destroying;

static void enter_queue(void) {
    pthread_mutex_lock(&graphics_state);
    while (graphics_suspended || device_destroying)
        pthread_cond_wait(&graphics_changed, &graphics_state);
    ++active_queue_calls;
    pthread_mutex_unlock(&graphics_state);
}
static void leave_queue(void) {
    pthread_mutex_lock(&graphics_state);
    --active_queue_calls;
    pthread_cond_broadcast(&graphics_changed);
    pthread_mutex_unlock(&graphics_state);
}
void biko3_vk_suspend(int suspended) {
    pthread_mutex_lock(&graphics_lifecycle);
    pthread_mutex_lock(&graphics_state);
    graphics_suspended = !!suspended;
    VkDevice device = active_device;
    if (suspended) {
        while (active_queue_calls) pthread_cond_wait(&graphics_changed, &graphics_state);
    } else pthread_cond_broadcast(&graphics_changed);
    pthread_mutex_unlock(&graphics_state);
    if (suspended && device) {
        VkResult result = vkDeviceWaitIdle(device);
        if (result != VK_SUCCESS) fprintf(stderr, "Biko3 background device idle: %d\n", result);
    }
    pthread_mutex_unlock(&graphics_lifecycle);
}
static VkResult queue_submit(VkQueue queue, uint32_t count, const VkSubmitInfo *submits, VkFence fence) {
    enter_queue();
    VkResult result = vkQueueSubmit(queue, count, submits, fence);
    leave_queue(); return result;
}
static VkResult queue_present(VkQueue queue, const VkPresentInfoKHR *present) {
    enter_queue();
    VkResult result = vkQueuePresentKHR(queue, present);
    leave_queue(); return result;
}
static VkResult device_wait_idle(VkDevice device) {
    enter_queue();
    VkResult result = vkDeviceWaitIdle(device);
    leave_queue(); return result;
}
static void destroy_device(VkDevice device, const VkAllocationCallbacks *alloc) {
    pthread_mutex_lock(&graphics_lifecycle);
    pthread_mutex_lock(&graphics_state);
    device_destroying = 1;
    while (active_queue_calls) pthread_cond_wait(&graphics_changed, &graphics_state);
    if (active_device == device) active_device = VK_NULL_HANDLE;
    pthread_mutex_unlock(&graphics_state);
    vkDestroyDevice(device, alloc);
    pthread_mutex_lock(&graphics_state);
    device_destroying = 0;
    pthread_cond_broadcast(&graphics_changed);
    pthread_mutex_unlock(&graphics_state);
    pthread_mutex_unlock(&graphics_lifecycle);
}

static PFN_vkVoidFunction queue_override(const char *name) {
    if (!strcmp(name, "vkQueueSubmit")) return (PFN_vkVoidFunction)queue_submit;
    if (!strcmp(name, "vkQueuePresentKHR")) return (PFN_vkVoidFunction)queue_present;
    if (!strcmp(name, "vkDestroyDevice")) return (PFN_vkVoidFunction)destroy_device;
    if (!strcmp(name, "vkDeviceWaitIdle")) return (PFN_vkVoidFunction)device_wait_idle;
    return NULL;
}

static int has_extension(const VkExtensionProperties *p, uint32_t n, const char *name) {
    for (uint32_t i = 0; i < n; ++i) if (!strcmp(p[i].extensionName, name)) return 1;
    return 0;
}
typedef VkResult (*ExtensionQuery)(void *, uint32_t *, VkExtensionProperties *);
static VkResult query_instance(void *context, uint32_t *count, VkExtensionProperties *properties) {
    return vkEnumerateInstanceExtensionProperties((const char *)context, count, properties);
}
static VkResult query_device(void *context, uint32_t *count, VkExtensionProperties *properties) {
    return vkEnumerateDeviceExtensionProperties((VkPhysicalDevice)context, NULL, count, properties);
}
/* Re-query count after VK_INCOMPLETE. Never trust a count that grew past the
 * allocated capacity; the alias entry uses one separately allocated element. */
static VkResult extension_snapshot(ExtensionQuery query, void *context,
                                   uint32_t *count, VkExtensionProperties **properties) {
    *properties = NULL; *count = 0;
    for (unsigned attempt = 0; attempt < 8; ++attempt) {
        uint32_t capacity = 0;
        VkResult result = query(context, &capacity, NULL);
        if (result != VK_SUCCESS) return result;
        if (capacity == UINT32_MAX) return VK_ERROR_OUT_OF_HOST_MEMORY;
        VkExtensionProperties *entries = calloc((size_t)capacity + 1, sizeof(*entries));
        if (!entries) return VK_ERROR_OUT_OF_HOST_MEMORY;
        uint32_t used = capacity;
        result = query(context, &used, entries);
        if (result == VK_SUCCESS && used <= capacity) {
            *properties = entries; *count = used; return VK_SUCCESS;
        }
        free(entries);
        if (result != VK_INCOMPLETE) return result == VK_SUCCESS ? VK_ERROR_INITIALIZATION_FAILED : result;
    }
    return VK_ERROR_INITIALIZATION_FAILED;
}
static VkResult instance_extensions(const char *layer, uint32_t *count, VkExtensionProperties *out) {
    if (!count) return VK_ERROR_INITIALIZATION_FAILED;
    uint32_t n = 0;
    VkExtensionProperties *all = NULL;
    VkResult r = extension_snapshot(query_instance, (void *)layer, &n, &all);
    if (r != VK_SUCCESS) return r;
    /* Advertise NN only when backed by the actual Metal surface extension. */
    if (has_extension(all, n, VK_EXT_METAL_SURFACE_EXTENSION_NAME) && !has_extension(all, n, NN_SURFACE)) {
        strcpy(all[n].extensionName, NN_SURFACE); all[n].specVersion = 1; ++n;
    }
    if (out) { uint32_t used = *count < n ? *count : n;
        memcpy(out, all, used * sizeof(*all)); *count = used;
        r = used < n ? VK_INCOMPLETE : VK_SUCCESS;
    } else *count = n;
    free(all); return r;
}

static VkResult create_instance(const VkInstanceCreateInfo *in, const VkAllocationCallbacks *alloc, VkInstance *out) {
    if (!in || !out) return VK_ERROR_INITIALIZATION_FAILED;
    uint32_t n = 0;
    VkExtensionProperties *available = NULL;
    VkResult r = extension_snapshot(query_instance, NULL, &n, &available);
    if (r != VK_SUCCESS) return r;
    const char **names = calloc((size_t)in->enabledExtensionCount + 2, sizeof(*names));
    if (!available || !names) { free(available); free(names); return VK_ERROR_OUT_OF_HOST_MEMORY; }
    VkInstanceCreateInfo info = *in;
    uint32_t used = 0;
    int portability = 0;
    for (uint32_t i = 0; i < in->enabledExtensionCount; ++i) {
        const char *name = in->ppEnabledExtensionNames[i];
        if (!strcmp(name, NN_SURFACE)) name = VK_EXT_METAL_SURFACE_EXTENSION_NAME;
        if (!strcmp(name, VK_KHR_PORTABILITY_ENUMERATION_EXTENSION_NAME)) portability = 1;
        int duplicate = 0;
        for (uint32_t j = 0; j < used; ++j) if (!strcmp(names[j], name)) duplicate = 1;
        if (!duplicate) names[used++] = name;
    }
    if (has_extension(available, n, VK_KHR_PORTABILITY_ENUMERATION_EXTENSION_NAME)) {
        if (!portability) names[used++] = VK_KHR_PORTABILITY_ENUMERATION_EXTENSION_NAME;
        info.flags |= VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR;
    }
    info.enabledExtensionCount = used; info.ppEnabledExtensionNames = names;
    r = vkCreateInstance(&info, alloc, out);
    free(names); free(available); return r;
}

static VkResult create_vi_surface(VkInstance instance, const GuestViSurfaceInfo *in,
                                  const VkAllocationCallbacks *alloc, VkSurfaceKHR *out) {
    const void *layer = atomic_load(&metal_layer);
    if (!in || !layer || in->sType != (VkStructureType)1000062000 || in->pNext || in->flags)
        return VK_ERROR_INITIALIZATION_FAILED;
    PFN_vkCreateMetalSurfaceEXT create = (PFN_vkCreateMetalSurfaceEXT)vkGetInstanceProcAddr(instance, "vkCreateMetalSurfaceEXT");
    if (!create) return VK_ERROR_EXTENSION_NOT_PRESENT;
    VkMetalSurfaceCreateInfoEXT info = { .sType = VK_STRUCTURE_TYPE_METAL_SURFACE_CREATE_INFO_EXT,
        .pLayer = (const CAMetalLayer *)layer };
    return create(instance, &info, alloc, out);
}

static VkResult create_device(VkPhysicalDevice physical, const VkDeviceCreateInfo *in,
                             const VkAllocationCallbacks *alloc, VkDevice *out) {
    if (!in || !out) return VK_ERROR_INITIALIZATION_FAILED;
    /* Replaces the original Mesa-NVK vendor restriction with real requirements.
     * Original features/pNext are preserved and validated by vkCreateDevice. */
    uint32_t queues = 0; vkGetPhysicalDeviceQueueFamilyProperties(physical, &queues, NULL);
    VkQueueFamilyProperties *families = calloc(queues ? queues : 1, sizeof(*families));
    if (!families) return VK_ERROR_OUT_OF_HOST_MEMORY;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queues, families);
    int graphics_compute = 0;
    for (uint32_t i = 0; i < in->queueCreateInfoCount; ++i) {
        uint32_t q = in->pQueueCreateInfos[i].queueFamilyIndex;
        if (q < queues && in->pQueueCreateInfos[i].queueCount &&
            (families[q].queueFlags & (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT)) ==
            (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT)) graphics_compute = 1;
    }
    free(families);
    if (!graphics_compute) return VK_ERROR_FEATURE_NOT_PRESENT;
    uint32_t n = 0;
    VkExtensionProperties *available = NULL;
    VkResult r = extension_snapshot(query_device, (void *)physical, &n, &available);
    if (r != VK_SUCCESS) return r;
    const char **names = calloc((size_t)in->enabledExtensionCount + 1, sizeof(*names));
    if (!available || !names) { free(available); free(names); return VK_ERROR_OUT_OF_HOST_MEMORY; }
    VkDeviceCreateInfo info = *in;
    uint32_t used = in->enabledExtensionCount;
    int portability = 0;
    for (uint32_t i = 0; i < used; ++i) {
        names[i] = in->ppEnabledExtensionNames[i];
        if (!strcmp(names[i], "VK_KHR_portability_subset")) portability = 1;
    }
    if (!portability && has_extension(available, n, "VK_KHR_portability_subset")) names[used++] = "VK_KHR_portability_subset";
    info.enabledExtensionCount = used; info.ppEnabledExtensionNames = names;
    r = vkCreateDevice(physical, &info, alloc, out);
    if (r == VK_SUCCESS) {
        pthread_mutex_lock(&graphics_lifecycle);
        pthread_mutex_lock(&graphics_state);
        int occupied = active_device != VK_NULL_HANDLE;
        if (!occupied) active_device = *out;
        pthread_mutex_unlock(&graphics_state);
        pthread_mutex_unlock(&graphics_lifecycle);
        if (occupied) {
            vkDestroyDevice(*out, alloc); *out = VK_NULL_HANDLE;
            r = VK_ERROR_TOO_MANY_OBJECTS;
        }
    }
    fprintf(stderr, "Biko3 Vulkan device creation: %d (real graphics + compute queue)\n", r);
    free(names); free(available); return r;
}

PFN_vkVoidFunction biko3_vk_get_instance_proc_addr(VkInstance instance, const char *name) {
    if (!name) return NULL;
    if (!strcmp(name, "vkGetInstanceProcAddr")) return (PFN_vkVoidFunction)biko3_vk_get_instance_proc_addr;
    if (!strcmp(name, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)biko3_vk_get_device_proc_addr;
    if (!strcmp(name, "vkEnumerateInstanceExtensionProperties")) return (PFN_vkVoidFunction)instance_extensions;
    if (!strcmp(name, "vkCreateInstance")) return (PFN_vkVoidFunction)create_instance;
    if (instance && !strcmp(name, "vkCreateViSurfaceNN")) return (PFN_vkVoidFunction)create_vi_surface;
    if (instance && !strcmp(name, "vkCreateDevice")) return (PFN_vkVoidFunction)create_device;
    if (instance) {
        PFN_vkVoidFunction override = queue_override(name);
        if (override && vkGetInstanceProcAddr(instance, name)) return override;
    }
    return vkGetInstanceProcAddr(instance, name);
}
PFN_vkVoidFunction biko3_vk_get_device_proc_addr(VkDevice device, const char *name) {
    if (!name) return NULL;
    if (!strcmp(name, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)biko3_vk_get_device_proc_addr;
    if (device) {
        PFN_vkVoidFunction override = queue_override(name);
        if (override && vkGetDeviceProcAddr(device, name)) return override;
    }
    return vkGetDeviceProcAddr(device, name);
}

/* Addresses are linked, rather than queried with a null Vulkan instance: most
 * Vulkan commands are intentionally unavailable from null-instance lookup. */
PFN_vkVoidFunction biko3_vk_resolve(const char *name) {
    if (!name) return NULL;
    PFN_vkVoidFunction override = queue_override(name);
    if (override) return override;
    if (!strcmp(name, "vkCreateInstance")) return (PFN_vkVoidFunction)create_instance;
    if (!strcmp(name, "vkCreateDevice")) return (PFN_vkVoidFunction)create_device;
    if (!strcmp(name, "vkCreateViSurfaceNN")) return (PFN_vkVoidFunction)create_vi_surface;
    if (!strcmp(name, "vkEnumerateInstanceExtensionProperties")) return (PFN_vkVoidFunction)instance_extensions;
    if (!strcmp(name, "vkGetInstanceProcAddr")) return (PFN_vkVoidFunction)biko3_vk_get_instance_proc_addr;
    if (!strcmp(name, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)biko3_vk_get_device_proc_addr;
#define BIKO3_VK_EXPORT(fn) if (!strcmp(name, #fn)) return (PFN_vkVoidFunction)fn;
#include "Biko3VulkanExports.inc"
#undef BIKO3_VK_EXPORT
    return NULL;
}

/* The cast required by vkGet*ProcAddr must not conceal an ABI mismatch. */
#define BIKO3_CHECK_API(function, type) \
    _Static_assert(_Generic(&(function), type: 1, default: 0), #function " Vulkan ABI mismatch")
BIKO3_CHECK_API(create_instance, PFN_vkCreateInstance);
BIKO3_CHECK_API(create_device, PFN_vkCreateDevice);
BIKO3_CHECK_API(destroy_device, PFN_vkDestroyDevice);
BIKO3_CHECK_API(instance_extensions, PFN_vkEnumerateInstanceExtensionProperties);
BIKO3_CHECK_API(queue_submit, PFN_vkQueueSubmit);
BIKO3_CHECK_API(queue_present, PFN_vkQueuePresentKHR);
BIKO3_CHECK_API(device_wait_idle, PFN_vkDeviceWaitIdle);
#undef BIKO3_CHECK_API
