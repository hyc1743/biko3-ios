#pragma once
#include <stddef.h>
#include <stdint.h>

/* The original libnx image continues to own newlib, TLS and C++ constructors. */
int biko3_libnx_boot(uint8_t *base, size_t image_size, uint32_t main_handle,
                     int argc, char **argv);
void biko3_libnx_bind(uint8_t *base, size_t image_size);
void *biko3_libnx_resolve(const char *name);
void biko3_libnx_app_init(void);
void *biko3_platform_create(int argc, char **argv, void *config, char *error);
int biko3_platform_poll(void *platform, void *state);
double biko3_platform_time(void *platform);
void biko3_platform_destroy(void *platform);
void biko3_platform_error(void *platform, const char *error);
void biko3_set_error_callback(void (*callback)(const char *));
/* Buttons use Biko3's game mask; axes are normalized; touch uses panel pixels. */
void biko3_input_update(uint32_t buttons, float lx, float ly, float rx, float ry,
                        float touch_x, float touch_y, int touching);
void biko3_input_active(int active);
int biko3_libnx_input_snapshot(void *out);
