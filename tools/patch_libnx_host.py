"""Generate the reviewed host main.c overlay for the Biko3 libnx startup ABI."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path

MAIN_SHA256 = "ea642c6e32214ba569000cfd22ba4dd49cda0295d5fc0ddcc832805adab6d3d4"


def patch_main(raw: bytes) -> bytes:
    if hashlib.sha256(raw).hexdigest() != MAIN_SHA256:
        raise ValueError("nxnative host main.c differs from the reviewed pinned source")
    text = raw.decode("utf8")
    begin = text.index("static void guest_main(void) {")
    end = text.index("\nstruct dm_flags g_flag;", begin)
    replacement = '''extern int biko3_libnx_boot(uint8_t *, size_t, uint32_t, int, char **);
static void guest_main(void) {
    guest_thread_init();
    uint32_t handle = svc_thread_main_init();
    if (g_nmod != 1 || strcmp(g_mod[0].name, "main"))
        die("Biko3 requires one main module");
    biko3_libnx_boot(g_mod[0].base, g_mod[0].size, handle, g_argc, g_argv);
}
'''
    return (text[:begin] + replacement + text[end:]).encode("utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nxnative-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = patch_main((args.nxnative_root / "dm/host/main.c").read_bytes())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(result)


if __name__ == "__main__":
    main()
