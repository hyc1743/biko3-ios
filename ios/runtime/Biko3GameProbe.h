#pragma once
#include <stddef.h>
#include <stdint.h>

/* Optional research hook. A zero original offset disables it in normal builds. */
void biko3_game_probe_bind(uint8_t *base, size_t size, size_t original_offset);
int biko3_game_probe_tick(double delta, void *process, const void *input, char *error);
