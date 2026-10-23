#include "Biko3GameProbe.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int (*Tick)(double, void *, const void *, char *);
static Tick original_tick;
static uint8_t *image;
static int trace_enabled;
static uint64_t ticks;
static uint8_t previous_mode = 0xff;
static uint32_t previous_bank = UINT32_MAX, previous_area = UINT32_MAX;

static uint32_t word(const uint8_t *p, size_t offset) {
    uint32_t result; memcpy(&result, p + offset, sizeof result); return result;
}
static uintptr_t pointer(const uint8_t *p, size_t offset) {
    uintptr_t result; memcpy(&result, p + offset, sizeof result); return result;
}
static double clock_at(const uint8_t *p, size_t offset) {
    double result; memcpy(&result, p + offset, sizeof result); return result;
}

void biko3_game_probe_bind(uint8_t *base, size_t size, size_t offset) {
    image = base;
    original_tick = NULL;
    if (offset) {
        if (!base || offset > size || size - offset < 8) abort();
        original_tick = (Tick)(base + offset);
    }
    const char *env = getenv("BIKO3_PROBE_TRACE");
    trace_enabled = env && !strcmp(env, "1");
    ticks = 0; previous_mode = 0xff;
    previous_bank = previous_area = UINT32_MAX;
}

int biko3_game_probe_tick(double delta, void *process, const void *input, char *error) {
    if (!original_tick) {
        if (error) snprintf(error, 256, "Game observation hook has no original entry");
        return 0;
    }
    int result = original_tick(delta, process, input, error);
    if (!trace_enabled || !process || !result) return result;
    const uint8_t *root = process;
    uint8_t mode = root[0xcc0];
    uint32_t bank = word(root, 0x30), area = word(root, 0x34);
    ticks++;
    if (mode != previous_mode || bank != previous_bank || area != previous_area || ticks % 120 == 0) {
        previous_mode = mode; previous_bank = bank; previous_area = area;
        fprintf(stderr, "[biko3-probe] frame=%llu mode=%u bank=%u area=%u clock=%.9f\n",
            (unsigned long long)ticks, mode, bank, area, clock_at(root, 0xa0828));
    }
    if (mode == 2) {
        const uint8_t *container = (const uint8_t *)pointer(root, 0xa07d8);
        if (container && pointer(container, 0) == (uintptr_t)(image + 0xc29c0) &&
            pointer(container, 0x20) == (uintptr_t)(image + 0x9de80)) {
            const uint8_t *live = (const uint8_t *)pointer(container, 0x18);
            if (live && word(live, 0x36f8) && !word(live, 0x3700) && ticks % 120 == 0)
                fprintf(stderr, "[biko3-probe] gameplay-active frame=%llu group=%u area=%u live_clock=%.9f\n",
                    (unsigned long long)ticks, word(live, 0x3288), word(live, 0x328c), clock_at(live, 0x36f0));
        }
    }
    return result;
}
