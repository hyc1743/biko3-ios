#include "Biko3QuickButtons.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define CHECK(expression) do { \
    if (!(expression)) { \
        fprintf(stderr, "quick buttons failed at line %d: %s\n", __LINE__, #expression); \
        exit(1); \
    } \
} while (0)
#define L3 BIKO3_QUICK_L3_MASK
#define R3 BIKO3_QUICK_R3_MASK
#define SECOND BIKO3_QUICK_R3_HOLD_NS

static void save_edges(void) {
    Biko3QuickButtons s;
    biko3_quick_buttons_init(&s, 0, 0);
    CHECK(biko3_quick_buttons_sample(&s, L3, 1) == BIKO3_QUICK_SAVE);
    CHECK(biko3_quick_buttons_sample(&s, L3, SECOND * 10) == 0);
    CHECK(biko3_quick_buttons_sample(&s, 0, SECOND * 10 + 1) == 0);
    CHECK(biko3_quick_buttons_sample(&s, L3, SECOND * 10 + 2) == BIKO3_QUICK_SAVE);
}
static void exact_hold_and_one_shot(void) {
    Biko3QuickButtons s;
    biko3_quick_buttons_init(&s, 0, 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, 123) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, 123 + SECOND - 1) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, 123 + SECOND) == BIKO3_QUICK_LOAD_CONFIRM);
    CHECK(biko3_quick_buttons_sample(&s, R3, 123 + SECOND) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, SECOND * 100) == 0);
    CHECK(biko3_quick_buttons_sample(&s, 0, SECOND * 100 + 1) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, SECOND * 100 + 2) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, SECOND * 101 + 2) == BIKO3_QUICK_LOAD_CONFIRM);
}
static void short_holds_do_not_accumulate(void) {
    Biko3QuickButtons s;
    biko3_quick_buttons_init(&s, 0, 0);
    for (uint64_t i = 0; i < 20; i++) {
        uint64_t start = (i + 1) * SECOND;
        CHECK(biko3_quick_buttons_sample(&s, R3, start) == 0);
        CHECK(biko3_quick_buttons_sample(&s, R3, start + SECOND - 1) == 0);
        CHECK(biko3_quick_buttons_sample(&s, 0, start + SECOND - 1) == 0);
    }
}
static void foreground_and_load_reset(void) {
    Biko3QuickButtons s;
    biko3_quick_buttons_init(&s, L3 | R3, 17);
    CHECK(biko3_quick_buttons_sample(&s, L3 | R3, SECOND * 60) == 0);
    CHECK(biko3_quick_buttons_sample(&s, 0, SECOND * 60 + 1) == 0);
    CHECK(biko3_quick_buttons_sample(&s, L3 | R3, SECOND * 60 + 2) == BIKO3_QUICK_SAVE);
    biko3_quick_buttons_reset(&s, L3 | R3, SECOND * 120);
    CHECK(biko3_quick_buttons_sample(&s, L3 | R3, SECOND * 180) == 0);
    CHECK(biko3_quick_buttons_sample(&s, 0, SECOND * 180 + 1) == 0);
    CHECK(biko3_quick_buttons_sample(&s, L3 | R3, SECOND * 180 + 2) == BIKO3_QUICK_SAVE);
    CHECK(biko3_quick_buttons_sample(&s, L3 | R3, SECOND * 181 + 2) == BIKO3_QUICK_LOAD_CONFIRM);
}
static void rollback_and_counter_wrap_are_not_holds(void) {
    Biko3QuickButtons s;
    biko3_quick_buttons_init(&s, 0, SECOND * 20);
    CHECK(biko3_quick_buttons_sample(&s, R3, SECOND * 21) == 0);
    CHECK(biko3_quick_buttons_sample(&s, L3 | R3, 2) == 0);
    CHECK(biko3_quick_buttons_sample(&s, L3 | R3, SECOND * 40) == 0);
    CHECK(biko3_quick_buttons_sample(&s, 0, SECOND * 40 + 1) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, SECOND * 40 + 2) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, SECOND * 41 + 2) == BIKO3_QUICK_LOAD_CONFIRM);

    biko3_quick_buttons_reset(&s, 0, UINT64_MAX - 100);
    CHECK(biko3_quick_buttons_sample(&s, R3, UINT64_MAX - 99) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, 1) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, SECOND * 3) == 0);
}
static void unrelated_buttons_and_simultaneous_events(void) {
    Biko3QuickButtons s;
    biko3_quick_buttons_init(&s, UINT64_MAX & ~(L3 | R3), 0);
    CHECK(biko3_quick_buttons_sample(&s, 0, 1) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, 2) == 0);
    CHECK(biko3_quick_buttons_sample(&s, UINT64_MAX & ~L3, SECOND) == 0);
    CHECK(biko3_quick_buttons_sample(&s, UINT64_MAX, SECOND + 2) ==
          (BIKO3_QUICK_SAVE | BIKO3_QUICK_LOAD_CONFIRM));
    CHECK(biko3_quick_buttons_sample(&s, UINT64_MAX, SECOND * 20) == 0);
}
static void no_hold_time_before_first_observation(void) {
    Biko3QuickButtons s;
    biko3_quick_buttons_init(&s, 0, 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, SECOND * 1000) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, SECOND * 1000) == 0);
    CHECK(biko3_quick_buttons_sample(&s, R3, SECOND * 1001) == BIKO3_QUICK_LOAD_CONFIRM);
}
int main(void) {
    save_edges();
    exact_hold_and_one_shot();
    short_holds_do_not_accumulate();
    foreground_and_load_reset();
    rollback_and_counter_wrap_are_not_holds();
    unrelated_buttons_and_simultaneous_events();
    no_hold_time_before_first_observation();
    puts("QUICK_BUTTONS_PASS cases=7");
    return 0;
}
