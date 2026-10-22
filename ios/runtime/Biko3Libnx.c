/* Biko3 build 31320a8c: offsets are verified by the offline hook manifest.
 * This file never rewrites executable pages and never relocates the image. */
#include "Biko3Libnx.h"
#include "Biko3FS.h"
#include "Biko3Audio.h"
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdatomic.h>

#define BIKO3_HOOK_TABLE 0xfac000u
#define BIKO3_INIT 0x510700u
#define BIKO3_MAIN 0x99280u
#define BIKO3_STACK_TOP 0xfa4688u
extern uint64_t svc_dispatch(int, uint64_t *, uint64_t);
extern void biko3_bind_guest_hooks(uint8_t *, size_t);
extern int Biko3InputSnapshot(void *);
extern uint64_t guest_ns(void);
extern void guest_clock_read_wait(void);
typedef struct { uint32_t key, flags; uint64_t value[2]; } ConfigEntry;
_Static_assert(sizeof(ConfigEntry) == 24, "libnx homebrew ABI");
static uint8_t *guest_base;
typedef void (*error_callback_fn)(const char *);
static _Atomic(error_callback_fn) error_callback;
static pthread_mutex_t input_lock = PTHREAD_MUTEX_INITIALIZER;
static struct {
    uint32_t held, previous;
    float axes[4], touch[2];
    int touching, active;
} input = {.active = 1};

/* __nx_exit restores the saved stack before branching here. There is no loader
 * to return to on iOS, so end this game worker without terminating UIKit. */
static void loader_return(uint64_t result) {
    fprintf(stderr, "[biko3] libnx returned result %#llx\n", (unsigned long long)result);
    pthread_exit((void *)(uintptr_t)result);
}

void biko3_libnx_bind(uint8_t *base, size_t size) {
    if (!base || size < BIKO3_HOOK_TABLE + 0x4000) abort();
    guest_base = base;
    uintptr_t *table = (uintptr_t *)(base + BIKO3_HOOK_TABLE);
    table[1024] = (uintptr_t)biko3_libnx_app_init;
    table[1025] = (uintptr_t)biko3_platform_create;
    table[1026] = (uintptr_t)biko3_platform_poll;
    table[1027] = (uintptr_t)biko3_platform_time;
    table[1028] = (uintptr_t)biko3_platform_time;
    table[1029] = (uintptr_t)biko3_platform_destroy;
    table[1030] = (uintptr_t)biko3_find_device;
}

void *biko3_libnx_resolve(const char *name) {
    if (!strcmp(name, "biko3_libnx_app_init")) return (void *)biko3_libnx_app_init;
    if (!strcmp(name, "biko3_platform_create")) return (void *)biko3_platform_create;
    if (!strcmp(name, "biko3_platform_poll")) return (void *)biko3_platform_poll;
    if (!strcmp(name, "biko3_platform_time")) return (void *)biko3_platform_time;
    if (!strcmp(name, "biko3_platform_destroy")) return (void *)biko3_platform_destroy;
    if (!strcmp(name, "biko3_find_device")) return (void *)biko3_find_device;
    if (!strcmp(name, "biko3_platform_error")) return (void *)biko3_platform_error;
    return biko3_audio_resolve(name);
}

int biko3_libnx_boot(uint8_t *base, size_t size, uint32_t handle,
                     int argc, char **argv) {
    biko3_libnx_bind(base, size);
    biko3_bind_guest_hooks(base, size);
    uint64_t r[32] = {0};
    r[1] = 512u * 1024u * 1024u;
    const char *heap_env = getenv("BIKO3_HEAP_MB");
    if (heap_env) {
        char *end = NULL;
        unsigned long mb = strtoul(heap_env, &end, 10);
        if (*end || mb < 64 || mb > 3072 || (mb & 1)) {
            fprintf(stderr, "[biko3] BIKO3_HEAP_MB must be even, 64..3072\n");
            return -1;
        }
        r[1] = (uint64_t)mb << 20;
    }
    size_t heap_size = (size_t)r[1];
    svc_dispatch(1, r, 0); /* one backing heap, accounted for by the host SVC layer */
    if (r[0] || !r[1]) {
        fprintf(stderr, "[biko3] heap setup failed %#llx\n", (unsigned long long)r[0]);
        return -1;
    }
    ConfigEntry config[] = {
        {1, 0, {handle, 0}},                     /* MainThreadHandle */
        {3, 0, {r[1], heap_size}},               /* OverrideHeap */
        {6, 0, {UINT64_MAX, UINT64_MAX}},         /* SyscallAvailableHint */
        {7, 0, {(uint64_t)(int64_t)-2, 0}},      /* AppletType_None */
        {16, 0, {0x0d0000, 0}},                 /* firmware 13.0.0 */
        {17, 0, {UINT64_MAX, 0}},                /* SyscallAvailableHint2 */
        {0, 0, {0, 0}}
    };
    uintptr_t stack;
#if defined(__aarch64__) || defined(__arm64__)
    __asm__ volatile("mov %0, sp" : "=r"(stack));
#else
    stack = (uintptr_t)&stack;
#endif
    *(uintptr_t *)(base + BIKO3_STACK_TOP) = stack;
    fprintf(stderr, "[biko3] libnx init +%#x, heap %zu MiB\n", BIKO3_INIT, heap_size >> 20);
    ((void (*)(const ConfigEntry *, uint64_t, void *))(base + BIKO3_INIT))(
        config, UINT64_MAX, (void *)loader_return);
    fprintf(stderr, "[biko3] original libnx init completed\n");
    /* Do not call run_module_inits: libnx's __libc_init_array has already run. */
    fprintf(stderr, "[biko3] entering original main +%#x\n", BIKO3_MAIN);
    int result = ((int (*)(int, char **))(base + BIKO3_MAIN))(argc, argv);
    fprintf(stderr, "[biko3] original main returned %d\n", result);
    return result;
}

void biko3_libnx_app_init(void) {
    /* Platform create/poll below own these services. No Horizon SM, applet, HID,
     * time, fsdevMountSdmc or libnx console service is started on Apple. */
    biko3_fs_install(guest_base);
    fprintf(stderr, "[biko3] platform services provided by iOS\n");
}

void *biko3_platform_create(int argc, char **argv, void *out, char *error) {
    (void)argc; (void)argv;
    const char *root = getenv("BIKO3_GAME_ROOT");
    if (!root) root = getenv("DM_ROMFS");
    const char *save = getenv("BIKO3_SAVE_ROOT");
    if (!save) save = getenv("DM_SAVE");
    if (!root || !*root || !save || !*save) {
        snprintf(error, 256, "game and save directories are required");
        return NULL;
    }
    /* Exact 48-byte output observed at a5e00: pointers at 0,8,16,32;
     * uint32 mode at 24 and uint32 option at 40. */
    memset(out, 0, 48);
    const char *game = root;
    const char *format = "game";
    memcpy((uint8_t *)out, &game, 8);
    memcpy((uint8_t *)out + 16, &format, 8);
    uint32_t mode = 1;
    memcpy((uint8_t *)out + 24, &mode, 4);
    memcpy((uint8_t *)out + 32, &save, 8);
    void *p = calloc(1, 64);
    if (!p) snprintf(error, 256, "platform allocation failed");
    return p;
}

void biko3_platform_destroy(void *platform) { free(platform); }
void biko3_set_error_callback(void (*callback)(const char *)) {
    atomic_store(&error_callback, callback);
}
void biko3_platform_error(void *platform, const char *error) {
    (void)platform;
    /* The original error path starts Horizon's text console and waits for HID.
     * Return the error to the native frontend and let the original main unwind. */
    fprintf(stderr, "[biko3] game error: %s\n", error ? error : "unknown");
    error_callback_fn callback = atomic_load(&error_callback);
    if (callback) callback(error ? error : "unknown");
}

void biko3_input_update(uint32_t held, float lx, float ly, float rx, float ry,
                        float tx, float ty, int touching) {
    pthread_mutex_lock(&input_lock);
    input.held = held;
    input.axes[0] = lx; input.axes[1] = ly;
    input.axes[2] = rx; input.axes[3] = ry;
    input.touch[0] = tx; input.touch[1] = ty; input.touching = !!touching;
    pthread_mutex_unlock(&input_lock);
}
void biko3_input_active(int active) {
    pthread_mutex_lock(&input_lock);
    input.active = !!active;
    if (!active) { input.held = 0; input.touching = 0; }
    pthread_mutex_unlock(&input_lock);
}
int biko3_platform_poll(void *platform, void *out) {
    (void)platform;
    return Biko3InputSnapshot(out);
}
/* Local portable state provider retained for host ABI tests and non-UIKit
 * frontends. The iOS app supplies Biko3InputSnapshot with this exact layout. */
int biko3_libnx_input_snapshot(void *out) {
    pthread_mutex_lock(&input_lock);
    memset(out, 0, 48);
    uint32_t buttons[3] = {input.held, input.held & ~input.previous,
                           input.previous & ~input.held};
    memcpy(out, buttons, sizeof buttons);
    memcpy((uint8_t *)out + 12, input.axes, sizeof input.axes);
    memcpy((uint8_t *)out + 28, input.touch, sizeof input.touch);
    ((uint8_t *)out)[44] = (uint8_t)input.touching;
    input.previous = input.held;
    int active = input.active;
    pthread_mutex_unlock(&input_lock);
    return active;
}
double biko3_platform_time(void *platform) {
    (void)platform;
    guest_clock_read_wait();
    return (double)guest_ns() * 1e-9;
}
