#include "Biko3QuickState.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

static int identity_equal(const Biko3QuickStateIdentity *a,
                          const Biko3QuickStateIdentity *b) {
    return a->root == b->root && a->live == b->live &&
           a->player == b->player && a->npc == b->npc &&
           a->items == b->items && a->group == b->group &&
           a->area == b->area && a->host_generation == b->host_generation;
}

void biko3_quick_state_init(Biko3QuickStateSlot *slot) {
    if (slot) memset(slot, 0, sizeof *slot);
}

void biko3_quick_state_dispose(Biko3QuickStateSlot *slot) {
    if (!slot) return;
    free(slot->payload);
    memset(slot, 0, sizeof *slot);
}

void biko3_quick_state_invalidate(Biko3QuickStateSlot *slot) {
    if (!slot) return;
    free(slot->payload);
    slot->payload = NULL;
    slot->length = 0;
    slot->game_clock = 0;
    slot->valid = 0;
    memset(&slot->identity, 0, sizeof slot->identity);
}

int biko3_quick_state_has_snapshot(const Biko3QuickStateSlot *slot) {
    return slot && slot->valid && slot->payload && slot->length > 0 &&
           slot->length <= BIKO3_QUICK_STATE_MAX_BYTES && isfinite(slot->game_clock);
}

Biko3QuickStateResult biko3_quick_state_capture(
    Biko3QuickStateSlot *slot, const Biko3QuickStateIdentity *identity,
    const void *payload, size_t length, double game_clock) {
    if (!slot || !identity || !payload) return BIKO3_QUICK_STATE_BAD_ARGUMENT;
    if (!length || length > BIKO3_QUICK_STATE_MAX_BYTES)
        return BIKO3_QUICK_STATE_BAD_SIZE;
    if (!isfinite(game_clock)) return BIKO3_QUICK_STATE_BAD_CLOCK;

    uint8_t *copy = malloc(length);
    if (!copy) return BIKO3_QUICK_STATE_BAD_ARGUMENT;
    memcpy(copy, payload, length);

    /* Commit only after allocation/copy completes. The prior slot remains
     * usable through every validation and allocation failure above. */
    uint8_t *old = slot->payload;
    slot->identity = *identity;
    slot->payload = copy;
    slot->length = length;
    slot->game_clock = game_clock;
    slot->valid = 1;
    free(old);
    return BIKO3_QUICK_STATE_OK;
}

Biko3QuickStateResult biko3_quick_state_restore(
    const Biko3QuickStateSlot *slot, const Biko3QuickStateIdentity *identity,
    void *target, size_t target_capacity, double *game_clock_out) {
    if (!slot || !identity || !target || !game_clock_out)
        return BIKO3_QUICK_STATE_BAD_ARGUMENT;
    if (!biko3_quick_state_has_snapshot(slot))
        return BIKO3_QUICK_STATE_NO_SNAPSHOT;
    if (!isfinite(slot->game_clock)) return BIKO3_QUICK_STATE_BAD_CLOCK;
    if (slot->length > BIKO3_QUICK_STATE_MAX_BYTES ||
        target_capacity != slot->length)
        return BIKO3_QUICK_STATE_BAD_SIZE;
    if (!identity_equal(&slot->identity, identity))
        return BIKO3_QUICK_STATE_IDENTITY_MISMATCH;

    /* Caller serializes access to this slot and target. Nothing below can fail. */
    memcpy(target, slot->payload, slot->length);
    *game_clock_out = slot->game_clock;
    return BIKO3_QUICK_STATE_OK;
}
