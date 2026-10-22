#!/usr/bin/env python3
"""Stage the pinned real guest, host adapters and signing-time embedded image."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from nro import Nro
from patch_libnx_host import patch_main
from prepare_nxnative import main as prepare
from rewrite_nro import main as rewrite, COMMIT, reviewed_sources
from patch_guest_hooks import main as patch_hooks

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "000000000000B103"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Reviewed host source anchor changed: " + old[:80])
    return text.replace(old, new, 1)


def binding_header(hooks, build_id):
    rows = []
    slots = set()
    for hook in hooks["hooks"]:
        symbol, slot = hook["symbol"], hook["slot"]
        if not symbol.replace("_", "").isalnum() or slot in slots:
            raise ValueError("Invalid or duplicate host binding")
        slots.add(slot)
        rows.append("    {" + json.dumps(symbol) + ", " + str(slot) + "},")
    build = ",".join("0x" + build_id[i:i+2] for i in range(0, 64, 2))
    return "\n".join([
        "/* Generated from verified offline hooks; do not edit. */", "#pragma once",
        "typedef struct { const char *symbol; unsigned slot; } Biko3HookBinding;",
        f"#define BIKO3_TABLE_OFFSET {hooks['table_offset']:#x}",
        f"#define BIKO3_TABLE_SIZE {hooks['table_size']:#x}",
        f"#define BIKO3_HOOK_COUNT {len(rows)}",
        f"static const unsigned char biko3_expected_build[32] = {{{build}}};",
        "static const Biko3HookBinding biko3_hook_bindings[] = {", *rows, "};", ""])


def frontend(text):
    text = '#include "Biko3Libnx.h"\n#import "Biko3ImportViewController.h"\n' + text
    text = text.replace('@"romfs"', '@"game"').replace('@"save"', '@"saves"')
    text = replace_once(text, 'for (NSString *d in @[@"saves", @"temp"])',
                        'for (NSString *d in @[@"saves", @"temp", @"game", @"captures", @"ResourcePacks"])')
    text = replace_once(text, 'static int boot_env(void) {', 'static int boot_env(int resources_ready) {')
    text = replace_once(text, 'for (NSString *d in @[@"game", @"temp"])',
                        'for (NSString *d in @[@"game", @"temp", @"ResourcePacks"])')
    text = replace_once(text, '    for (NSString *d in @[@"saves",',
                        '    static int initialized;\n    if (!initialized) {\n    initialized = 1;\n    for (NSString *d in @[@"saves",')
    text = replace_once(text, '    NSString *romfs = romfs_locate();',
                        '    }\n    if (!resources_ready) return 0;\n    NSString *romfs = romfs_locate();')
    text = replace_once(text, 'setenv("DM_ROMFS", romfs.fileSystemRepresentation, 0);',
                        'setenv("DM_ROMFS", romfs.fileSystemRepresentation, 0);\n'
                        '    setenv("BIKO3_GAME_ROOT", romfs.fileSystemRepresentation, 1);\n'
                        '    setenv("BIKO3_SAVE_ROOT", [doc stringByAppendingPathComponent:@"saves"].fileSystemRepresentation, 1);\n'
                        '    setenv("BIKO3_CAPTURE_ROOT", [doc stringByAppendingPathComponent:@"captures"].fileSystemRepresentation, 1);')
    text = replace_once(text, 'setvbuf(stderr, NULL, _IOLBF, 0);',
                        'setvbuf(stderr, NULL, _IOLBF, 0);\n    dup2(fileno(stderr), STDOUT_FILENO);')
    error_ui = '''static void biko3_show_error(const char *message) {
    @autoreleasepool {
        NSString *text = message ? [NSString stringWithUTF8String:message] : @"Game startup failed.";
        dispatch_async(dispatch_get_main_queue(), ^{
            UIViewController *controller = nil;
            for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
                if (![scene isKindOfClass:UIWindowScene.class]) continue;
                for (UIWindow *window in ((UIWindowScene *)scene).windows)
                    if (window.isKeyWindow) controller = window.rootViewController;
            }
            while (controller.presentedViewController) controller = controller.presentedViewController;
            if (!controller) return;
            UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"Biko3 could not start"
                message:[text stringByAppendingString:@"\\n\\nCheck Documents/game/Data and Documents/log.txt."]
                preferredStyle:UIAlertControllerStyleAlert];
            [alert addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];
            [controller presentViewController:alert animated:YES completion:nil];
        });
    }
}
'''
    text = replace_once(text, 'int main(int argc, char **argv) {', error_ui + '\nint main(int argc, char **argv) {')
    text = replace_once(text, '@autoreleasepool { g_boot_ok = boot_env(); }',
                        '@autoreleasepool { biko3_set_error_callback(biko3_show_error); boot_env(0); }')
    text = replace_once(text, '    self.window.rootViewController = front_ios_root();', '''    __weak UIWindow *window = self.window;
    self.window.rootViewController = [[Biko3ImportViewController alloc] initWithReady:^{
        g_boot_ok = boot_env(1);
        if (!g_boot_ok || !window) return NO;
        window.rootViewController = front_ios_root();
        front_ios_focus_event(3);
        return YES;
    }];''')
    return text


def gamepad(text):
    """Use the Biko3 diamond on both touch and positional GameController input."""
    text = replace_once(text,
        'switch (b) { case B_A: return CGPointMake(c.x + g, c.y); case B_B: return CGPointMake(c.x, c.y + g); case B_X: return CGPointMake(c.x, c.y - g); default: return CGPointMake(c.x - g, c.y); }',
        'switch (b) { case B_A: return CGPointMake(c.x, c.y + g); case B_B: return CGPointMake(c.x + g, c.y); case B_X: return CGPointMake(c.x - g, c.y); default: return CGPointMake(c.x, c.y - g); }')
    text = replace_once(text,
        'K(g.buttonB, B_A); K(g.buttonA, B_B); K(g.buttonY, B_X); K(g.buttonX, B_Y);   // by position: right A, bottom B, top X, left Y',
        'K(g.buttonA, B_A); K(g.buttonB, B_B); K(g.buttonX, B_X); K(g.buttonY, B_Y);   // by position: bottom A, right B, left X, top Y')
    # Keep the staged source's layout notes consistent with its actual controls.
    text = text.replace('A (right) B (bottom) X (top) Y (left)', 'A (bottom) B (right) X (left) Y (top)')
    text = text.replace('bottom = B, right = A, left = Y, top = X (same positions as on a Switch controller)',
                        'bottom = A, right = B, left = X, top = Y (Biko3 layout)')
    return text


def patch_sources(source, profile, runtime):
    host = source / "dm/host"
    (host / "main.c").write_bytes(patch_main((host / "main.c").read_bytes()))
    for path in sorted(runtime.iterdir()):
        if path.suffix in {".c", ".h", ".inc"}:
            shutil.copyfile(path, host / path.name)
    make = (host / "Makefile").read_text(encoding="utf8")
    names = " ".join(path.name for path in sorted(runtime.glob("*.c")))
    make = replace_once(make, "SRC=main.c loader.c", f"SRC={names} main.c loader.c")
    make = replace_once(make, "../gfx/vk/*.m", "")
    (host / "Makefile").write_text(make, encoding="utf8")
    hid = (host / "hle_hid.c").read_text(encoding="utf8")
    hid += '''
/* Biko3 adapter: sample the real virtual/physical controller merge and latches. */
void biko3_read_native_pad(uint64_t *buttons, int32_t axes[4]) {
    NpadState state;
    uint32_t id = connected_id();
    fill(&state, id, style_of(id));
    *buttons = state.buttons;
    axes[0] = state.lx; axes[1] = state.ly;
    axes[2] = state.rx; axes[3] = state.ry;
}
'''
    (host / "hle_hid.c").write_text(hid, encoding="utf8")
    # The upstream front end manages the touch/controller UI and iOS lifecycle.
    ui = source / "dm/ios/Sources"
    (ui / "DmGamepad.m").write_text(gamepad((ui / "DmGamepad.m").read_text(encoding="utf8")), encoding="utf8")
    (ui / "main.m").write_text(frontend((ui / "main.m").read_text(encoding="utf8")), encoding="utf8")
    front = (ui / "front_ios.m").read_text(encoding="utf8")
    front = '#include "Biko3Vulkan.h"\n#include "Biko3Audio.h"\n' + front
    front = front.replace('Put the RomFS folder into this App\'s Documents as \\"romfs\\"',
                          'Put the original game folder, containing Data, into Documents as \\"game\\"')
    front = replace_once(front, "static void front_ios_audio_apply(void) { dm_audio_suspend(g_audio_bg || g_audio_intr); }",
                         "static void front_ios_audio_apply(void) { dm_audio_suspend(g_audio_bg || g_audio_intr); biko3_audio_suspend(g_audio_bg || g_audio_intr); }")
    front = replace_once(front, "case 1: UIApplication.sharedApplication.idleTimerDisabled = NO;",
                         "case 1: biko3_vk_suspend(1); UIApplication.sharedApplication.idleTimerDisabled = NO;")
    front = replace_once(front, "case 2: dm_gfx_suspend(0);", "case 2: biko3_vk_suspend(0); dm_gfx_suspend(0);")
    (ui / "front_ios.m").write_text(front, encoding="utf8")
    for path in (ROOT / "ios/profile").iterdir():
        shutil.copyfile(path, profile / path.name)


def stage(args):
    original, out = args.nro.resolve(), args.out.resolve()
    upstream = args.nxnative_root.resolve()
    if out.is_relative_to(upstream) or upstream.is_relative_to(ROOT / "ios/runtime"):
        raise ValueError("Staging output must be separate from pinned upstream")
    reviewed_sources(upstream)
    source = out / "work/nxnative-staged"
    if source.exists() or (out / "ios").exists():
        raise ValueError("Staging directories already exist")
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(upstream, source, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    image = Nro(original.read_bytes())
    prepared, rewritten, hooked = out / "prepared", out / "rewritten", out / "rewritten-bindings"
    if prepare([str(original), "--out", str(prepared)]) != 0:
        raise ValueError("NRO preparation failed")
    if rewrite([str(original), "--prepared", str(prepared), "--nxnative-root", str(upstream), "--out", str(rewritten)]) != 0:
        raise ValueError("Guest rewriting failed")
    merged = []
    for name in ("vulkan-hooks.json", "libnx-hooks.json"):
        spec = json.loads((ROOT / "ios/runtime" / name).read_text(encoding="utf8"))
        if spec["source_nro_sha256"] != image.sha256:
            raise ValueError("Hook profile is bound to a different guest")
        merged.extend(spec["hooks"])
    combined = out / "guest-hooks.json"
    combined.write_text(json.dumps({"source_nro_sha256": image.sha256, "hooks": merged}, indent=2), encoding="utf8")
    patch_hooks(["--nro", str(original), "--rewritten", str(rewritten / "rewritten/main.patched.bin"),
                 "--provenance", str(rewritten / "provenance.json"), "--boundaries", str(prepared / "main.function-boundaries.json"),
                 "--hooks", str(combined), "--out", str(hooked)])
    hooks = json.loads((hooked / "hooks.json").read_text(encoding="utf8"))
    profile = source / "dm/games" / PROFILE
    profile.mkdir(parents=True)
    patch_sources(source, profile, ROOT / "ios/runtime")
    (profile / "Biko3HookTable.h").write_text(binding_header(hooks, image.build_id), encoding="utf8")
    ios = out / "ios"
    shutil.copytree(source / "dm/ios/Sources", ios / "Sources")
    shutil.copytree(ROOT / "ios/Assets.xcassets", ios / "Sources/Assets.xcassets")
    for path in (ROOT / "ios/import").iterdir():
        if path.suffix in {".h", ".m"}:
            shutil.copyfile(path, ios / "Sources" / path.name)
    shutil.copyfile(ROOT / "ios/project.yml", ios / "project.yml")
    gen = ios / "Gen"
    dm = gen / "dm"
    dm.mkdir(parents=True)
    shutil.copyfile(ROOT / "ios/resources/Biko3Data.manifest.json", dm / "Biko3Data.manifest.json")
    for path in hooked.glob("*.tsv"):
        shutil.copyfile(path, dm / path.name)
    for name in ("shader_map.tsv", "pso_manifest.tsv"):
        shutil.copyfile(profile / name, dm / name)
    notices = dm / "notices"
    notices.mkdir()
    shutil.copyfile(upstream / "LICENSE", notices / "nxnative-MIT.txt")
    environment = dict(os.environ, GAME=PROFILE, DM_GAME=PROFILE)
    environment.pop("DM_GAMES_FIRST", None)
    environment.pop("DM_GAMES_DIR", None)
    environment.pop("BIKO3_GUEST_KEY", None)
    embed = subprocess.run([sys.executable, str(upstream / "dm/ios/tools/gen_embed.py"), str(hooked), str(gen / "embed.S"), "main"],
                           capture_output=True, text=True, check=True, env=environment)
    if not args.prepare_only:
        mvk = Path(os.environ["MOLTENVK_ROOT"]).resolve()
        library = mvk / "static/MoltenVK.xcframework/ios-arm64/libMoltenVK.a"
        if not library.is_file():
            raise ValueError("MOLTENVK_ROOT does not contain the iOS static library")
        config = subprocess.check_output([sys.executable, str(upstream / "dm/ios/tools/gen_build.py"),
                                          str(source / "dm/host"), str(gen)], text=True, env=environment)
        config = replace_once(config, 'HEADER_SEARCH_PATHS = $(inherited) ',
                              'HEADER_SEARCH_PATHS = $(inherited) "' + str(mvk / "include") + '" ')
        config += 'OTHER_LDFLAGS = $(inherited) $(DM_FRAMEWORKS) ' + embed.stdout.strip()
        config += ' "' + str(library) + '" -lc++ -framework Metal -framework QuartzCore -framework IOSurface -framework Foundation -framework AVFAudio -framework UIKit -framework CoreMotion -framework CoreText\n'
        (gen / "dm.xcconfig").write_text(config, encoding="utf8")
    report = {"guest_sha256": image.sha256, "guest_embedded": True, "source_commit": COMMIT,
              "project": "ios/project.yml", "host_root": "work/nxnative-staged/dm/host",
              "patched_dir": "rewritten-bindings", "hook_count": len(hooks["hooks"]),
              "patched_sha256": sha(hooked / "main.patched.bin"),
              "assembly_sha256": sha(gen / "embed.S"), "project_sources_generated": not args.prepare_only,
              "compiled": False, "runtime_verified": False}
    (out / "stage-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nxnative-root", type=Path, required=True)
    parser.add_argument("--nro", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true", help="Generate guest/source inputs without macOS make or Xcode")
    stage(parser.parse_args(argv))


if __name__ == "__main__":
    main()
