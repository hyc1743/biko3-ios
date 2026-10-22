/* Convert the existing touch/GameController front end to the exact 48-byte
 * Biko3 state observed in original a5a90 and its a5890 button mapper. */
#include <stdint.h>
#include <string.h>
#include "Biko3Libnx.h"

void biko3_read_native_pad(uint64_t *buttons, int32_t axes[4]);
int touch_fill_n(void *out, int max, uint64_t now);
uint64_t now_ns(void);
void guest_clock_read_wait(void);

uint32_t biko3_map_buttons(uint64_t buttons) {
    static const uint64_t masks[] = {
        0x1, 0x2, 0x400, 0x2000, 0x8000, 0x1000, 0x4000, 0x4, 0x8,
        0x80, 0x4, 0x8, 0x1, 0x2, 0x100, 0x40, 0x80, 0x800
    };
    uint32_t result = 0;
    for (unsigned i = 0; i < sizeof masks / sizeof masks[0]; ++i)
        if (buttons & masks[i]) result |= 1u << i;
    return result;
}

int Biko3InputSnapshot(void *output) {
    guest_clock_read_wait();
    uint64_t buttons;
    int32_t axes[4];
    biko3_read_native_pad(&buttons, axes);
    uint8_t touch[0x38] = {0};
    int count = touch_fill_n(touch, 1, now_ns());
    int32_t x = 0, y = 0;
    memcpy(&x, touch + 0x20, sizeof x);
    memcpy(&y, touch + 0x24, sizeof y);
    /* The original poll reports panel pixels as floats, not normalized UVs.
     * Its no-touch fallback is exactly (320,240). */
    biko3_input_update(biko3_map_buttons(buttons),
        axes[0] / 32768.0f, axes[1] / 32768.0f,
        axes[2] / 32768.0f, axes[3] / 32768.0f,
        count ? (float)x : 320.0f, count ? (float)y : 240.0f, count != 0);
    return biko3_libnx_input_snapshot(output);
}
