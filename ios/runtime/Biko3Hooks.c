/* Executable redirects are generated before signing. Only this data table is
 * populated at startup; no instruction page is modified on the device. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "Biko3Libnx.h"
#include "Biko3Vulkan.h"
#include "Biko3GameProbe.h"
#include "Biko3HookTable.h"

void *front_metal_layer(void);

void biko3_bind_guest_hooks(uint8_t *base, size_t size) {
    if (!base || size < BIKO3_TABLE_OFFSET + BIKO3_TABLE_SIZE ||
        memcmp(base + 0x10, "NRO0", 4) ||
        memcmp(base + 0x40, biko3_expected_build, sizeof biko3_expected_build)) {
        fprintf(stderr, "[biko3] embedded guest identity/layout mismatch\n");
        abort();
    }
    biko3_vk_set_metal_layer(front_metal_layer());
    biko3_game_probe_bind(base, size, BIKO3_GAME_PROBE_ORIGINAL_OFFSET);
    uintptr_t *table = (uintptr_t *)(base + BIKO3_TABLE_OFFSET);
    memset(table, 0, BIKO3_TABLE_SIZE);
    for (size_t i = 0; i < BIKO3_HOOK_COUNT; ++i) {
        const Biko3HookBinding *hook = &biko3_hook_bindings[i];
        uintptr_t target = hook->slot < 1024
            ? (uintptr_t)biko3_vk_resolve(hook->symbol)
            : (uintptr_t)biko3_libnx_resolve(hook->symbol);
        if (!target || hook->slot >= BIKO3_TABLE_SIZE / sizeof(*table)) {
            fprintf(stderr, "[biko3] unresolved native hook %s\n", hook->symbol);
            abort();
        }
        table[hook->slot] = target;
    }
    fprintf(stderr, "[biko3] bound %zu hooks, writable table +%#x\n",
            (size_t)BIKO3_HOOK_COUNT, BIKO3_TABLE_OFFSET);
}
