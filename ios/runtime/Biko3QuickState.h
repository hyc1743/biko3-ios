#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BIKO3_QUICK_STATE_MAX_BYTES (32u * 1024u)

typedef struct {
    uintptr_t root;
    uintptr_t live;
    uintptr_t player;
    uintptr_t npc;
    uintptr_t items;
    uint32_t group;
    uint32_t area;
    uint64_t host_generation;
} Biko3QuickStateIdentity;

typedef enum {
    BIKO3_QUICK_STATE_OK = 0,
    BIKO3_QUICK_STATE_NO_SNAPSHOT = 1,
    BIKO3_QUICK_STATE_BAD_ARGUMENT = 2,
    BIKO3_QUICK_STATE_BAD_CLOCK = 3,
    BIKO3_QUICK_STATE_BAD_SIZE = 4,
    BIKO3_QUICK_STATE_IDENTITY_MISMATCH = 5
} Biko3QuickStateResult;

typedef struct {
    Biko3QuickStateIdentity identity;
    uint8_t *payload;
    size_t length;
    double game_clock;
    uint32_t valid;
} Biko3QuickStateSlot;

/* Single-thread-owned state. This module performs no file I/O, allocation from
 * game memory, UIKit work, clock reads or host-generation changes. */
void biko3_quick_state_init(Biko3QuickStateSlot *slot);
void biko3_quick_state_dispose(Biko3QuickStateSlot *slot);
void biko3_quick_state_invalidate(Biko3QuickStateSlot *slot);
int biko3_quick_state_has_snapshot(const Biko3QuickStateSlot *slot);

/* Capture makes a deep copy and only replaces an existing slot after every
 * argument and the full copy have succeeded. A failed capture retains the old
 * snapshot. game_clock must be finite. */
Biko3QuickStateResult biko3_quick_state_capture(
    Biko3QuickStateSlot *slot, const Biko3QuickStateIdentity *identity,
    const void *payload, size_t length, double game_clock);

/* Restore validates the complete identity, size and stored clock before its
 * single-thread memcpy commit. On failure, target and output clock are intact.
 * Successful restore leaves the snapshot reusable for repeated loads. */
Biko3QuickStateResult biko3_quick_state_restore(
    const Biko3QuickStateSlot *slot, const Biko3QuickStateIdentity *identity,
    void *target, size_t target_capacity, double *game_clock_out);

#ifdef __cplusplus
}
#endif
