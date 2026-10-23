# Offline NRO preparation for nxnative

Run from the repository root with Python 3.11 or newer (the tools use hashlib.file_digest):

```powershell
python tools/prepare_nxnative.py switch/biko3/biko3-runtime.nro --out work/biko3-ios/nxnative-preparation
python -m unittest discover -s tests -v
```

Use a fresh output directory on each run. The tool refuses existing carrier or manifest files. No network access, key material, NSP extraction, or game execution is involved.

The outputs are `main.nso`, `main.preparation.json`, and, when recoverable, `main.function-boundaries.json`. The NSO is an **offline carrier**, not a runnable iOS app. Its new NSO header precedes the original text, rodata, and data bytes. Each segment keeps its original virtual offset. The original NRO entry branch, header, MOD0, dynamic table, RELR table and in-place relocation addends remain intact. BSS remains zero-fill metadata; the appended NRO asset footer is excluded. All 32 build ID bytes are retained. SHA256 values identify the original complete NRO, carrier, and each segment.

Validation rejects malformed NRO layouts, unknown NRO flags, missing MOD0/dynamic metadata, invalid MOD0 ranges, and malformed RELR or dynamic-symbol tables. The generated NSO is independently checked against the original segments before writing.

## Recovering function ranges

This Biko3 build has an empty MOD0 `eh_frame_hdr` range. Four zero bytes at that offset precede a real DWARF `.eh_frame` record stream. `recover_eh_frame(image)` validates the zero marker, CIE records, augmentation lengths, backwards CIE references, FDE ranges, and zero terminator within the original file-backed segment. It accepts CIE versions 1 and 3 with `z`, `P`, `L`, `R` augmentation and selected fixed-width pointer encodings. Unsupported encodings raise a descriptive error; it does not evaluate unwind opcodes or dereference personality pointers.

The boundary JSON contains integer offsets:

```json
{
  "schema": 1,
  "source_nro_sha256": "...",
  "eh_frame_start": 15223352,
  "eh_frame_end": 15900960,
  "eh_frame_sha256": "...",
  "cie_count": 3,
  "function_count": 13639,
  "functions": [
    {"start": 304, "size": 4840, "fde_offset": 15372304, "cie_offset": 15223352}
  ]
}
```

The example shows the first recovered range; the full artifact contains all entries. Each reported function is supported by an FDE, rather than guessed from instructions. Functions without FDE records are absent. The source NRO SHA256 and `.eh_frame` byte-range SHA256 bind the result to its evidence.

## Reviewed upstream rewriter

Optionally record hashes from an already downloaded nxnative source directory:

```powershell
python tools/prepare_nxnative.py switch/biko3/biko3-runtime.nro --out work/biko3-ios/prepare-with-source --nxnative-root references/seedds--nxnative
```

`--rewrite` permits execution only when `dm/tools/patch_nso.py` and `dm/tools/nso_code.py` exactly match the reviewed SHA256 pair embedded in this tool. It also checks the known EH header prerequisite before executing anything. Source files are never modified; Python bytecode writes are disabled; any execution uses a temporary output directory outside upstream. A successful subprocess must produce all expected tables and an image with the original NRO header, rodata, data, segment offsets and build ID before its output is accepted. Runtime behavior is not validated.

For this Biko3 build, **the original upstream rewriter is blocked**, even though the NSO conversion succeeds. `patch_nso.py:655-676` unconditionally treats the empty MOD0 range as an encoded `eh_frame_hdr`; its decoder does not support the zero encoding encountered there. `--rewrite` therefore records `executed: false` and exits with code 2, retaining the carrier and evidence manifest. The separate `rewrite_nro.py` compatibility overlay consumes the recovered FDE evidence to address this profile; see that tool's own validation and report.

The compatibility path has now actually succeeded, including upstream assembly generation:

```powershell
python tools/rewrite_nro.py switch/biko3/biko3-runtime.nro --prepared work/biko3-ios/nxnative-preparation --nxnative-root references/seedds--nxnative --out work/biko3-ios/rewrite-run-02
```

Use a fresh output directory. See `work/biko3-ios/nxnative/provenance.json` for the existing real run and `overlay.diff` for the two source changes. Assembly generation is verified; compilation and device execution are not. Its absolute incbin paths must be regenerated on the macOS build machine. Reference versions are fixed in the root `references.lock.json`.

`dm/host/loader.c` supports DT_RELR. Its symbol-count inference `(strtab - symtab) / 24` matches this build's SysV hash count of 3; the manifest checks this assumption independently. Original libnx entry/startup, TLS, heap, service calls, rendering, sound, input, assets and saves still need iOS integration. A successful offline rewrite is not evidence of a game boot or a rendered frame.

## Observed local result

The original NRO SHA256 is `31320a8ce44f8e1826fc90ed5b26cb714c551413b286f8c69adb03f9e8b07178`, with entry offset `0x80`, a `0xc000` BSS extent and 12,952 RELR targets. The tool recovered 13,639 FDE ranges from 3 CIE records. The observed carrier and manifest are under `work/biko3-ios/nxnative-preparation/`. Automated tests cover footer exclusion, exact segment preservation, corruption rejection, malformed NRO/RELR metadata, DWARF recovery and malformed FDE rejection, and CLI output/refusal to overwrite.
