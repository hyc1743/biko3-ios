#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BIKO3_QUICK_L3_MASK UINT64_C(0x10)
#define BIKO3_QUICK_R3_MASK UINT64_C(0x20)
#define BIKO3_QUICK_R3_HOLD_NS UINT64_C(1000000000)

enum {
    BIKO3_QUICK_NONE = 0,
    BIKO3_QUICK_SAVE = 1u << 0,
    BIKO3_QUICK_LOAD_CONFIRM = 1u << 1
};

/* Host-owned input state. Use on one thread; never restore it from guest memory.
 * Time is host monotonic nanoseconds, independent of the game clock. */
typedef struct {
    uint64_t previous_buttons;
    uint64_t r3_down_ns;
    uint64_t last_sample_ns;
    uint32_t r3_suppressed;
    uint32_t r3_fired;
} Biko3QuickButtons;

/* Establish a baseline without emitting events. Already-held L3/R3 are ignored
 * until released. Reset on foreground/controller reconnect and after a load. */
void biko3_quick_buttons_init(Biko3QuickButtons *state, uint64_t raw_buttons,
                               uint64_t host_monotonic_ns);
void biko3_quick_buttons_reset(Biko3QuickButtons *state, uint64_t raw_buttons,
                                uint64_t host_monotonic_ns);

/* Return an event bitmask. L3 fires on its rising edge; R3 fires once per hold,
 * after one second. LOAD_CONFIRM requests confirmation; it never performs a load.
 * Both bits can be returned together; the caller owns action serialization.
 * A backward timestamp resets the baseline and emits nothing for that sample. */
uint32_t biko3_quick_buttons_sample(Biko3QuickButtons *state,
                                    uint64_t raw_buttons,
                                    uint64_t host_monotonic_ns);

#ifdef __cplusplus
}
#endif
