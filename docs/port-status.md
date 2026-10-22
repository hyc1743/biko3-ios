# Port status

This is a development native iOS port, not a verified playable release.

The build embeds the supplied guest program into signed executable sections.
The host owns relocation once, initializes the original libnx/newlib runtime,
and uses native adapters for files, input and PCM audio. Actual Vulkan graphics
and compute APIs are forwarded to MoltenVK. Original game data is imported
separately into Documents/game/Data.

Offline staging has generated 104 static hooks and the real embedded image.
Portable tests pass. Apple compilation is performed by the public repository's
macOS Actions workflow and produces an unsigned development IPA plus a report.
The build report explicitly distinguishes compilation, runtime and playability.

Device startup, first frame, complete gameplay, sound, saves and background
recovery require execution evidence. They are not established by unit tests,
static analysis or an IPA being generated.
