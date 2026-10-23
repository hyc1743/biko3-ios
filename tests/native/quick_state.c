#include "Biko3QuickState.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(x) do { if (!(x)) { \
    fprintf(stderr, "QUICK_STATE_FAIL line=%d expression=%s\n", __LINE__, #x); exit(1); \
} } while (0)

static Biko3QuickStateIdentity identity(void) {
    return (Biko3QuickStateIdentity){
        .root = 0x1000, .live = 0x2000, .player = 0x3000,
        .npc = 0x4000, .items = 0x5000, .group = 7, .area = 12,
        .host_generation = 99
    };
}

static void deep_copy_and_repeat_restore(void) {
    Biko3QuickStateSlot slot; biko3_quick_state_init(&slot);
    Biko3QuickStateIdentity id = identity();
    uint8_t source[257], target[257];
    for (size_t i = 0; i < sizeof source; ++i) source[i] = (uint8_t)(i * 29u);
    CHECK(biko3_quick_state_capture(&slot, &id, source, sizeof source, 123.5) == BIKO3_QUICK_STATE_OK);
    memset(source, 0xee, sizeof source);
    memset(target, 0xa5, sizeof target);
    double clock = -1;
    CHECK(biko3_quick_state_restore(&slot, &id, target, sizeof target, &clock) == BIKO3_QUICK_STATE_OK);
    CHECK(clock == 123.5);
    for (size_t i = 0; i < sizeof target; ++i) CHECK(target[i] == (uint8_t)(i * 29u));
    memset(target, 0x55, sizeof target);
    CHECK(biko3_quick_state_restore(&slot, &id, target, sizeof target, &clock) == BIKO3_QUICK_STATE_OK);
    CHECK(clock == 123.5 && target[128] == (uint8_t)(128u * 29u));
    CHECK(biko3_quick_state_has_snapshot(&slot));
    biko3_quick_state_dispose(&slot);
    CHECK(!biko3_quick_state_has_snapshot(&slot));
}

static void identity_mismatch_is_transactional(void) {
    Biko3QuickStateSlot slot; biko3_quick_state_init(&slot);
    Biko3QuickStateIdentity id = identity(), other = id;
    uint8_t source[16], target[16], before[16]; memset(source, 0x17, sizeof source);
    CHECK(biko3_quick_state_capture(&slot, &id, source, sizeof source, 9) == BIKO3_QUICK_STATE_OK);
    memset(target, 0xc3, sizeof target); memcpy(before, target, sizeof before);
    double clock = 987;
    for (unsigned field = 0; field < 8; ++field) {
        other = id;
        switch (field) {
            case 0: other.root++; break; case 1: other.live++; break;
            case 2: other.player++; break; case 3: other.npc++; break;
            case 4: other.items++; break; case 5: other.group++; break;
            case 6: other.area++; break; default: other.host_generation++; break;
        }
        CHECK(biko3_quick_state_restore(&slot, &other, target, sizeof target, &clock) == BIKO3_QUICK_STATE_IDENTITY_MISMATCH);
        CHECK(memcmp(target, before, sizeof target) == 0 && clock == 987);
    }
    biko3_quick_state_dispose(&slot);
}

static void invalid_capture_preserves_old_snapshot(void) {
    Biko3QuickStateSlot slot; biko3_quick_state_init(&slot);
    Biko3QuickStateIdentity id = identity(); uint8_t source[4] = {1,2,3,4};
    CHECK(biko3_quick_state_capture(&slot, &id, source, sizeof source, 1) == BIKO3_QUICK_STATE_OK);
    CHECK(biko3_quick_state_capture(&slot, &id, source, sizeof source, NAN) == BIKO3_QUICK_STATE_BAD_CLOCK);
    CHECK(biko3_quick_state_capture(&slot, &id, source, sizeof source, INFINITY) == BIKO3_QUICK_STATE_BAD_CLOCK);
    CHECK(biko3_quick_state_capture(&slot, &id, source, sizeof source, -INFINITY) == BIKO3_QUICK_STATE_BAD_CLOCK);
    CHECK(biko3_quick_state_capture(&slot, &id, source, 0, 2) == BIKO3_QUICK_STATE_BAD_SIZE);
    CHECK(biko3_quick_state_capture(&slot, &id, source, BIKO3_QUICK_STATE_MAX_BYTES + 1u, 2) == BIKO3_QUICK_STATE_BAD_SIZE);
    uint8_t target[4] = {0}; double clock = 0;
    CHECK(biko3_quick_state_restore(&slot, &id, target, sizeof target, &clock) == BIKO3_QUICK_STATE_OK);
    CHECK(memcmp(target, source, sizeof source) == 0 && clock == 1);
    biko3_quick_state_dispose(&slot);
}

static void replace_and_mismatch_lengths(void) {
    Biko3QuickStateSlot slot; biko3_quick_state_init(&slot);
    Biko3QuickStateIdentity id = identity();
    const uint8_t a[] = {1,2,3}, b[] = {8,9,10,11,12}; uint8_t target[8];
    CHECK(biko3_quick_state_capture(&slot, &id, a, sizeof a, 1) == BIKO3_QUICK_STATE_OK);
    CHECK(biko3_quick_state_capture(&slot, &id, b, sizeof b, 2) == BIKO3_QUICK_STATE_OK);
    memset(target, 0x64, sizeof target); uint8_t before[sizeof target]; memcpy(before, target, sizeof target);
    double clock = 7;
    CHECK(biko3_quick_state_restore(&slot, &id, target, sizeof target, &clock) == BIKO3_QUICK_STATE_BAD_SIZE);
    CHECK(memcmp(target, before, sizeof target) == 0 && clock == 7);
    CHECK(biko3_quick_state_restore(&slot, &id, target, sizeof b, &clock) == BIKO3_QUICK_STATE_OK);
    CHECK(memcmp(target, b, sizeof b) == 0 && clock == 2);
    biko3_quick_state_invalidate(&slot);
    CHECK(biko3_quick_state_restore(&slot, &id, target, sizeof b, &clock) == BIKO3_QUICK_STATE_NO_SNAPSHOT);
    biko3_quick_state_dispose(&slot);
}

int main(void) {
    deep_copy_and_repeat_restore();
    identity_mismatch_is_transactional();
    invalid_capture_preserves_old_snapshot();
    replace_and_mismatch_lengths();
    puts("QUICK_STATE_ALL_PASSED cases=4");
    return 0;
}
