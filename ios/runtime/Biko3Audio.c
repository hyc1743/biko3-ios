/* Native PCM transport for the original Biko3 mixer. Guest mixer/decoder,
 * buffering logic and libnx worker thread remain intact. Only audout is hosted. */
#include "Biko3Audio.h"
#include <AudioToolbox/AudioToolbox.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    void *next, *data;
    uint64_t capacity, length, offset;
} GuestAudioBuffer;
_Static_assert(sizeof(GuestAudioBuffer) == 40, "libnx AudioOutBuffer ABI");
static struct {
    pthread_mutex_t lock;
    pthread_mutex_t control;
    AudioQueueRef queue;
    GuestAudioBuffer *released[32];
    unsigned head, count, pending;
    int requested_start, started, suspended;
    OSStatus error;
} audio = {.lock = PTHREAD_MUTEX_INITIALIZER, .control = PTHREAD_MUTEX_INITIALIZER};

static void consumed(void *ctx, AudioQueueRef queue, AudioQueueBufferRef buffer) {
    (void)ctx;
    pthread_mutex_lock(&audio.lock);
    if (audio.count < 32) {
        audio.released[(audio.head + audio.count) % 32] = buffer->mUserData;
        audio.count++;
    } else audio.error = -1;
    if (audio.pending) audio.pending--;
    pthread_mutex_unlock(&audio.lock);
    AudioQueueFreeBuffer(queue, buffer);
}
static uint32_t audio_initialize_impl(void) {
    if (audio.queue) return 0;
    AudioStreamBasicDescription format = {
        .mSampleRate = 48000, .mFormatID = kAudioFormatLinearPCM,
        .mFormatFlags = kLinearPCMFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked,
        .mBytesPerPacket = 4, .mFramesPerPacket = 1, .mBytesPerFrame = 4,
        .mChannelsPerFrame = 2, .mBitsPerChannel = 16
    };
    audio.head = audio.count = audio.pending = 0;
    audio.requested_start = audio.started = 0;
    audio.error = AudioQueueNewOutput(&format, consumed, NULL, NULL,
                                      kCFRunLoopCommonModes, 0, &audio.queue);
    if (audio.error) {
        fprintf(stderr, "[biko3] AudioQueueNewOutput failed %d\n", (int)audio.error);
        return 0x1f59;
    }
    fprintf(stderr, "[biko3] native audio 48000 Hz stereo signed16\n");
    return 0;
}
static uint32_t audio_start_impl(void) {
    if (!audio.queue) return 0x1f59;
    audio.requested_start = 1;
    return 0;
}
static uint32_t audio_stop_impl(void) {
    if (!audio.queue) return 0;
    audio.requested_start = 0;
    OSStatus result = AudioQueueStop(audio.queue, true);
    audio.started = 0;
    return result ? 0x1f59 : 0;
}
static void audio_exit_impl(void) {
    if (!audio.queue) return;
    audio_stop_impl();
    AudioQueueDispose(audio.queue, true);
    audio.queue = NULL;
    audio.head = audio.count = audio.pending = 0;
}
static uint32_t audio_rate(void) { return 48000; }
static uint32_t audio_channels(void) { return 2; }
static uint32_t audio_format(void) { return 2; } /* PcmInt16 */

void biko3_audio_suspend(int suspended) {
    /* UIKit lifecycle owns this transition. AudioQueuePause preserves the queued
     * guest buffers and their completion order. No fake completions are emitted. */
    pthread_mutex_lock(&audio.control);
    if (audio.suspended == !!suspended) {
        pthread_mutex_unlock(&audio.control);
        return;
    }
    audio.suspended = !!suspended;
    if (!audio.queue || (!audio.started && (suspended || !audio.requested_start))) {
        pthread_mutex_unlock(&audio.control);
        return;
    }
    OSStatus result = suspended ? AudioQueuePause(audio.queue)
                                : AudioQueueStart(audio.queue, NULL);
    if (!result && !suspended) audio.started = 1;
    if (result) {
        pthread_mutex_lock(&audio.lock); audio.error = result; pthread_mutex_unlock(&audio.lock);
        fprintf(stderr, "[biko3] audio lifecycle failed %d\n", (int)result);
    }
    pthread_mutex_unlock(&audio.control);
}

static uint32_t audio_append_impl(GuestAudioBuffer *guest) {
    if (!audio.queue || !guest || !guest->data || guest->length != 1920 ||
        guest->offset > guest->capacity || guest->length > guest->capacity - guest->offset)
        return 0x1f59;
    AudioQueueBufferRef buffer;
    OSStatus result = AudioQueueAllocateBuffer(audio.queue, (UInt32)guest->length, &buffer);
    if (result) return 0x1f59;
    memcpy(buffer->mAudioData, (uint8_t *)guest->data + guest->offset, (size_t)guest->length);
    buffer->mAudioDataByteSize = (UInt32)guest->length;
    buffer->mUserData = guest;
    pthread_mutex_lock(&audio.lock);
    audio.pending++;
    pthread_mutex_unlock(&audio.lock);
    result = AudioQueueEnqueueBuffer(audio.queue, buffer, 0, NULL);
    if (result) {
        pthread_mutex_lock(&audio.lock); audio.pending--; pthread_mutex_unlock(&audio.lock);
        AudioQueueFreeBuffer(audio.queue, buffer);
        return 0x1f59;
    }
    if (audio.requested_start && !audio.started && !audio.suspended) {
        result = AudioQueueStart(audio.queue, NULL);
        if (!result) audio.started = 1;
    }
    return result ? 0x1f59 : 0;
}
/* Control calls may run on the guest worker or UIKit's interruption handler.
 * Callback completion uses only audio.lock, never the control lock. */
static uint32_t audio_initialize(void) {
    pthread_mutex_lock(&audio.control);
    uint32_t result = audio_initialize_impl();
    pthread_mutex_unlock(&audio.control); return result;
}
static uint32_t audio_start(void) {
    pthread_mutex_lock(&audio.control);
    uint32_t result = audio_start_impl();
    pthread_mutex_unlock(&audio.control); return result;
}
static uint32_t audio_stop(void) {
    pthread_mutex_lock(&audio.control);
    uint32_t result = audio_stop_impl();
    pthread_mutex_unlock(&audio.control); return result;
}
static void audio_exit(void) {
    pthread_mutex_lock(&audio.control); audio_exit_impl(); pthread_mutex_unlock(&audio.control);
}
static uint32_t audio_append(GuestAudioBuffer *guest) {
    pthread_mutex_lock(&audio.control);
    uint32_t result = audio_append_impl(guest);
    pthread_mutex_unlock(&audio.control); return result;
}
static uint32_t audio_released(GuestAudioBuffer **out, uint32_t *count) {
    if (!out || !count) return 0x1f59;
    pthread_mutex_lock(&audio.lock);
    *out = NULL; *count = 0;
    if (audio.count) {
        *out = audio.released[audio.head];
        audio.head = (audio.head + 1) % 32; audio.count--;
        *count = 1;
    }
    uint32_t result = audio.error ? 0x1f59 : 0;
    pthread_mutex_unlock(&audio.lock);
    return result;
}
void *biko3_audio_resolve(const char *name) {
#define AUDIO(n, f) if (!strcmp(name, n)) return (void *)f
    AUDIO("biko3_audio_initialize", audio_initialize);
    AUDIO("biko3_audio_exit", audio_exit);
    AUDIO("biko3_audio_rate", audio_rate);
    AUDIO("biko3_audio_channels", audio_channels);
    AUDIO("biko3_audio_format", audio_format);
    AUDIO("biko3_audio_start", audio_start);
    AUDIO("biko3_audio_stop", audio_stop);
    AUDIO("biko3_audio_append", audio_append);
    AUDIO("biko3_audio_released", audio_released);
#undef AUDIO
    return NULL;
}
