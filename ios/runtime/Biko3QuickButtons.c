#include "Biko3QuickButtons.h"

void biko3_quick_buttons_reset(Biko3QuickButtons *state, uint64_t buttons,
                                uint64_t now) {
    if (!state) return;
    *state = (Biko3QuickButtons){
        .previous_buttons = buttons,
        .r3_down_ns = now,
        .last_sample_ns = now,
        .r3_suppressed = (buttons & BIKO3_QUICK_R3_MASK) != 0,
        .r3_fired = 0
    };
}

void biko3_quick_buttons_init(Biko3QuickButtons *state, uint64_t buttons,
                               uint64_t now) {
    biko3_quick_buttons_reset(state, buttons, now);
}

uint32_t biko3_quick_buttons_sample(Biko3QuickButtons *state,
                                    uint64_t buttons, uint64_t now) {
    if (!state) return BIKO3_QUICK_NONE;
    if (now < state->last_sample_ns) {
        biko3_quick_buttons_reset(state, buttons, now);
        return BIKO3_QUICK_NONE;
    }
    uint32_t events = BIKO3_QUICK_NONE;
    if ((buttons & BIKO3_QUICK_L3_MASK) &&
        !(state->previous_buttons & BIKO3_QUICK_L3_MASK))
        events |= BIKO3_QUICK_SAVE;

    if (!(buttons & BIKO3_QUICK_R3_MASK)) {
        state->r3_down_ns = now;
        state->r3_suppressed = 0;
        state->r3_fired = 0;
    } else if (!(state->previous_buttons & BIKO3_QUICK_R3_MASK)) {
        state->r3_down_ns = now;
        state->r3_suppressed = 0;
        state->r3_fired = 0;
    } else if (!state->r3_suppressed && !state->r3_fired &&
               now - state->r3_down_ns >= BIKO3_QUICK_R3_HOLD_NS) {
        state->r3_fired = 1;
        events |= BIKO3_QUICK_LOAD_CONFIRM;
    }
    state->previous_buttons = buttons;
    state->last_sample_ns = now;
    return events;
}
