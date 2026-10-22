# Biko3 libnx startup

This implementation targets only the NRO SHA-256
`31320a8ce44f8e1826fc90ed5b26cb714c551413b286f8c69adb03f9e8b07178`.
The original game bytes and references are not changed. The cloud staging step
creates a host main.c overlay and applies signed-code hook trampolines offline.

The nxnative loader alone clears the newly allocated BSS and applies RELA/RELR.
The original NRO entry at 0x80 must **not** run: its __nx_dynamic at 0x5102f0
would clear initialized BSS and add the base to RELR pointers a second time.
The host initializes the rewritten guest thread/TLR and main-thread handle, then
calls `biko3_libnx_boot`. That enters 0x510700 with a libnx ConfigEntry array,
x1=UINT64_MAX, and an explicit loader-return callback in x2. The config includes
the real host thread handle and a heap allocated through the existing host SVC
layer. The guest still performs envSetup, newlibSetup, virtmemSetup, heap setup,
libnx thread setup, argv setup and its original C/C++ constructors. The host
does not additionally run DT_INIT. After init, it calls main's original thunk
0x99280 with argc/argv and logs its return value.

The weak default __appInit at 0x5105c0 is replaced by native device registration.
This avoids starting Horizon SM/applet/HID/time/fs/console IPC. The original
game platform functions at 0xa5e00/create, 0xa5a90/poll,
0xa5b70+0xa5bb0/time and 0xa5d60/destroy use the iOS implementation. The output
config is 48 bytes. Poll's held/down/up masks are at 0/4/8, four axis floats at
12/16/20/24, two touch floats at 28/32, and touch-valid byte at 44.

The game retains its own newlib FILE/_reent and handle manager. Native callbacks
implement the public devkitPro devoptab ABI, installed at the guest's existing
devoptab_list (0xf9a910). The FindDevice hook chooses that device for absolute
paths. A 104-byte guest stat structure is explicitly marshalled from Apple
struct stat; no Apple FILE or struct stat is handed to the guest. Native open,
read/write, seek, stat, directories, rename, mkdir, truncate and fsync callbacks
support game files and save/capture paths. Common newlib open flags and errno
values are translated. Guest stdout/stderr use the native application log.

Build integration:

```
python tools/patch_libnx_host.py --nxnative-root STAGED_REFERENCE --out STAGED_REFERENCE/dm/host/main.c
```

Compile `ios/runtime/Biko3Libnx.c` and `Biko3FS.c`. Merge `libnx-hooks.json` into
the shared static hook generation. The frontend provides
`void biko3_bind_guest_hooks(uint8_t *, size_t)` and
`int Biko3InputSnapshot(void *out48)`; the latter fills the exact poll layout and
returns whether the game should continue polling. Resolve libnx hook symbols
using `biko3_libnx_resolve`. Metal binding is owned by the frontend/Vulkan bridge.
Configure BIKO3_GAME_ROOT, BIKO3_SAVE_ROOT, BIKO3_CAPTURE_ROOT before boot.
BIKO3_HEAP_MB may select an even heap size from 64 to 3072 MiB; the default is 512.

Also compile `Biko3Audio.c` and link AudioToolbox.framework. Nine audout hooks
retain the game's original mixer, decoder, guest libnx audio thread and its
four-buffer playback accounting. The native transport copies each 480-frame
48 kHz stereo signed-16 PCM block to AudioQueue, and reports the original guest
buffer as released only after the Apple playback callback consumes it. Native
callbacks do not call guest code. Initialization/append/queue failures return
failure to the original game's error flow rather than reporting silent success.
The audout ABI and mixer worker paths are recorded in `ghidra-libnx-audio`,
`ghidra-libnx-audio-callbacks` and `ghidra-libnx-audout` under the evidence tree.

Evidence: bounded Ghidra exports in
`work/biko3-ios/evidence/ghidra-libnx-startup`, `ghidra-libnx-platform` and
`ghidra-libnx-files`; no guest execution occurred during analysis. ABI sources
were retrieved from switchbrew/libnx `nx/source/runtime/init.c` and
`switch_crt0.s`, and devkitPro/newlib **devkitPro branch**
`newlib/libc/include/sys/iosupport.h`, `sys/stat.h`, `sys/_types.h`,
`libgloss/libsysbase/iosupport.c` and `open.c`. AArch64's machine types do not
override the relevant generic stat fields. The observed guest 0x68-byte stat
buffer and devoptab deviceData offset 0xb0 independently match these definitions.

Current limits: static ABI/patch validation is not proof of boot. Apple compile
and physical iOS launch remain necessary to verify graphics, sound and
playability. Save operations are direct POSIX writes and have the game's own
atomicity; this is not the reference SDK's transactional save emulation.

`tools/mac_guest_smoke.py --staged OUT --moltenvk-root MVK --out OUT/logs/guest-macos`
builds the staged macOS host and executes the same rewritten guest through its
dynamic loader on a native arm64 Mac. It uses a fresh empty game/Data folder and
requires real logs for completed libnx init, original main entry, the original
newlib stat callback reporting missing patch.pp, original main return and normal
host process exit. Other operating systems/architectures report skipped with
verified=false. This check does not establish an iOS device boot or playability.

Optional `--windowed --out OUT/logs/guest-renderer` uses the real AppKit window
and CAMetalLayer from front_mac. It additionally requires the original game's
`Renderer ready:` log and original main returning 1 because assets are absent.
The staged frontend exits AppKit normally after the guest returns only when
the smoke-test environment flag is present. Production Mac completion behavior
is preserved. Run this second mode only after native GPU availability has been
verified; its GUI/renderer failure remains explicit evidence rather than a pass
or an inference about iOS execution.

The native platform error handler replaces the Horizon console/HID wait with a
logged error and an optional frontend callback (`biko3_set_error_callback`). The
game's original main then unwinds normally. The platform configuration's +32
field is a save-storage root: its consumers append `/save`, `volume.cfg`,
`unlocks.bku` and `records.bkr`; it now uses BIKO3_SAVE_ROOT.
