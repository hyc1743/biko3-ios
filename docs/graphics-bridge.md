# Biko3 Vulkan to Metal bridge

The iOS renderer links MoltenVK and forwards the game's actual Vulkan calls,
including both compute pipelines and dispatches. This replaces the original
Switch Mesa driver; it does not substitute a renderer with no compute support.
No iOS render or device execution has yet been verified.

The inspected official dependency is MoltenVK **v1.4.2**, `MoltenVK-ios.tar`,
SHA256 `b5d947b1660e6e9fed40b9cd2387e160aaab9e80b775c0cef7e14059405178c1`.
The downloaded archive was checked against the official release asset digest.
Its static library is
`MoltenVK/MoltenVK/static/MoltenVK.xcframework/ios-arm64/libMoltenVK.a` and
headers are in `MoltenVK/MoltenVK/include`. No downloaded executable or build
script was run. The static library also requires the C++ runtime at link time.

## Evidence and hook coverage

The supported NRO SHA256 is
`31320a8ce44f8e1826fc90ed5b26cb714c551413b286f8c69adb03f9e8b07178`.
The `ghidra-vulkan-init/decompiled/d5370.asm` name lookup export has SHA256
`54ec412915f04908e26bd784a9ae3a4d284926e9f745e99b54388b2ae0330690`.
Its repeated string comparison and relocated pointer lookup yield 764 mapped
Vulkan export entry points. The renderer's direct wrappers dereference Mesa
dispatch tables at offsets inside the handle (e.g. `d2440` uses physical device
offset `0x1380`). Real MoltenVK handles cannot pass through these wrappers.

`tools/patch_vulkan.py` scans B/BL throughout the game prefix `[0x80,0xd23c0)`
and emits hooks for each referenced mapped wrapper, two procedure query hooks,
and independently analyzed instance lifecycle exports. The current manifest
has 88 hooks and includes `vkCreateComputePipelines` and `vkCmdDispatch`.
Dynamic procedure queries return real MoltenVK addresses through the bridge.
This is a bounded static scan, not proof of complete runtime call coverage.

## Host integration

Compile `ios/runtime/Biko3Vulkan.c` against MoltenVK's Vulkan headers, link
MoltenVK plus Metal/QuartzCore/IOSurface frameworks, and retain the generated
`Biko3VulkanExports.inc` beside the C source. For every `kind=vulkan` record in
the combined hooks manifest, resolve `symbol` with `biko3_vk_resolve` and write
the nonnull function pointer at `guest_base + pointer_offset` **before entering
guest code**. Fail initialization if any required pointer is null. Resolution
uses linked API symbols, so instance/device commands are available before the
first guest call without passing a null instance to Vulkan's procedure query.

Call `biko3_vk_set_metal_layer` with the retained CAMetalLayer from the UI.
The guest NN window pointer is not dereferenced. `vkCreateViSurfaceNN` creates
an actual `VK_EXT_metal_surface` surface using that layer, preserving allocation
callbacks; unsupported flags/pNext fail explicitly.

The instance extension adapter advertises NN only when MoltenVK advertises
Metal surface support. Instance creation translates NN to Metal surface and
enables advertised portability enumeration. Device creation adds advertised
portability subset, requires an actual requested graphics+compute queue, and
passes original features, extension requirements, and pNext to the real Vulkan
implementation. Unsupported capabilities fail; no device features are forged.

The UI calls `biko3_vk_suspend(1)` when resigning active, before background
execution, and `biko3_vk_suspend(0)` on foreground activation. The bridge closes
the queue gate first, waits for already entered submit/present/guest-idle calls
to leave, then calls actual `vkDeviceWaitIdle` without its state mutex held.
Resume wakes blocked guest calls. A separate lifecycle mutex serializes the
host idle operation with device destruction; no state mutex crosses a Vulkan
call. Static resolution and both Vulkan procedure-query routes return these
same wrappers. The one-device renderer is tracked explicitly; a second device
is rejected rather than left outside background synchronization. Neither
`vkQueueSubmit2` nor sparse binding appears in the bounded game call manifest,
so those APIs have no added lifecycle support.

Extension snapshots re-query after `VK_INCOMPLETE` rather than trusting a count
that changed between allocation and enumeration. The NN alias is appended to
the actual extension set, retaining the Metal extension. Mapping NN and Metal
names during creation removes the resulting duplicate requests.

`tests/native/vulkan_smoke.c` is an optional real-GPU test containing an original
no-op compute shader and no game data. It creates a real compute pipeline,
dispatches it, waits on a fence, and checks a submit worker blocks during
suspend and wakes after resume. With the iOS dependency headers alone, CI can
still compile-check this file and `Biko3Vulkan.c` using `clang -std=c11
-fsyntax-only -I <MoltenVK include>`. Actual smoke execution requires a matching
macOS MoltenVK library. Compile-time C11 type assertions check wrapper signatures
against Vulkan PFN types before procedure-address casts erase their types.

## Static patching

`patch_guest_hooks.py` accepts the original NRO, reviewed rewrite provenance,
FDE manifest, and combined hook list. It verifies the exact original/rewrite
hashes, checks all hooks are original FDE text entries, verifies a completely
empty 16 KiB table gap at `0xfac000`, and emits a new rewritten directory.
Each entry gets one static branch to an appended, aligned ADRP/LDR/BR stub
using only x16. Original gates and metadata offsets remain stable. Slots
0..1023 are reserved for Vulkan; libnx begins at 1024. The host writes pointer
data only; no runtime executable-memory mutation or JIT is introduced.

The original driver-ID rejection at `0xd0ddc` is changed from `212f0054` to
NOP after exact source and rewritten byte assertions. The bridge checks real
queue capabilities and leaves actual shader/pipeline creation and Vulkan
feature validation intact. Shader translation or pipeline failure remains a
real porting failure, not a success stub.

After patching, regenerate the upstream embedding assembly from the new
`main.patched.bin` and copied metadata. The generated hooks manifest binds
input/output hashes, original bytes, rewritten entry bytes, stub offsets, and
pointer slots. Unit tests decode the actual AArch64 instructions to verify
signed branch reach, ADRP slot addressing, and x16 usage, and check that
unverified inputs and non-FDE hooks fail closed. iOS compilation and device
rendering remain separate required validation steps.
