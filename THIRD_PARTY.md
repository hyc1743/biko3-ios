# Third-party components

This repository's MIT license covers its original adapter and tooling code.
It does not relicense the game executable, game assets or upstream projects.

| Component | Source and fixed version | Use |
|---|---|---|
| Biko3 Switch runtime | ruje0504-2/biko3-switch-runtime, 63fddc3830b991e725657689934c3b6e3b5786bb (source reference); supplied NRO 1.0.79, SHA-256 31320a8ce44f8e1826fc90ed5b26cb714c551413b286f8c69adb03f9e8b07178 | Original Switch port underlying the embedded guest; game data and patches retain their original rights. See docs/upstream-status.md and the upstream notices for provenance and component licenses |
| nxnative | seedds/nxnative, 677345b0933f5f8754aeec3125d14df32683ca97, MIT | Native host, signed section loader, offline rewriter, input UI and system services |
| MoltenVK | KhronosGroup/MoltenVK v1.4.2, Apache-2.0 | Vulkan graphics and compute on Metal |
| devkitPro libnx/newlib | Public ABI declarations, verified against the supplied executable | Homebrew startup, devoptab/stat and PCM-buffer interface documentation |

nxnative and its bundled libraries retain their respective licenses. Builds
download the fixed source and preserve its MIT license in the app's notices.
MoltenVK's release includes its licenses and third-party acknowledgements;
the staging process includes those notices when available. Python analysis
and transport dependencies retain the licenses of their installed packages.

No Switch console keys or NSP files are required by this build. Game data is
imported separately into the installed app's Documents/game/Data directory.
