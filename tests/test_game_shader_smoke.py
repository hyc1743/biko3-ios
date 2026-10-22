import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from game_shader_smoke import EXPECTED, reflect_compute, run_native
from nro import NroError


def fixture(*, block=3, include_binding=True, push=True, matrix_stride=True,
            member_offset=True, descriptor_set=0, storage=2):
    """Synthetic reflection-only module; never supplied to the game pipeline probe."""
    words = [0x07230203, 0x10000, 0, 100, 0]

    def op(code, *args): words.extend([((len(args)+1) << 16) | code, *args])

    op(17, 1)
    op(14, 0, 1)
    op(15, 5, 50, 0x6e69616d, 0)
    op(16, 50, 17, 64, 1, 1)
    op(71, 7, block)
    op(72, 7, 0, 35, 0)
    op(71, 9, 34, descriptor_set)
    if include_binding: op(71, 9, 33, 2)
    if push:
        op(71, 10, 2)
        op(72, 10, 0, 5)  # ColMajor
        if matrix_stride: op(72, 10, 0, 7, 16)
        if member_offset: op(72, 10, 0, 35, 0)
        op(72, 10, 1, 35, 64)
        op(72, 10, 2, 35, 68)
    op(19, 1)
    op(33, 2, 1)
    op(22, 3, 32)
    op(23, 4, 3, 4)
    op(24, 5, 4, 4)
    op(21, 6, 32, 0)
    op(30, 7, 3)
    op(32, 8, storage, 7)
    op(59, 8, 9, storage)
    if push:
        op(30, 10, 5, 6, 6)
        op(32, 11, 9, 10)
        op(59, 11, 12, 9)
    op(54, 1, 50, 0, 2)
    op(248, 51)
    op(253)
    op(56)
    return struct.pack(f"<{len(words)}I", *words)


class GameShaderReflectionTests(unittest.TestCase):
    def test_bufferblock_and_matrix_push_offsets_have_exact_extent(self):
        reflected = reflect_compute(fixture())
        self.assertEqual(reflected["local_size"], [64, 1, 1])
        self.assertEqual(reflected["bindings"][0]["descriptor_type"], 7)
        self.assertEqual(reflected["bindings"][0]["binding"], 2)
        self.assertEqual(reflected["push_constants"], [{"offset": 0, "size": 72,
            "members": [{"offset": 0, "size": 64}, {"offset": 64, "size": 4}, {"offset": 68, "size": 4}]}])

    def test_uniform_storage_class_alone_does_not_mean_uniform_buffer(self):
        reflected = reflect_compute(fixture(block=2, push=False))
        self.assertEqual(reflected["bindings"][0]["descriptor_type"], 6)
        self.assertEqual(reflected["push_constants"], [])

    def test_missing_explicit_layout_evidence_fails_instead_of_guessing(self):
        for arguments, message in (({"include_binding": False}, "Binding"),
                                   ({"matrix_stride": False}, "MatrixStride"),
                                   ({"member_offset": False}, "Offset"),
                                   ({"descriptor_set": 1}, "descriptor set zero"),
                                   ({"storage": 0}, "unsupported resource")):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(NroError, message):
                reflect_compute(fixture(**arguments))

    def test_truncated_instruction_and_wrong_stage_are_not_accepted(self):
        with self.assertRaises(NroError): reflect_compute(fixture()[:-4])
        raw = bytearray(fixture())
        # Find the synthetic OpEntryPoint and change GLCompute to Vertex.
        for i in range(20, len(raw), 4):
            if struct.unpack_from("<I", raw, i)[0] == 0x0005000f:
                struct.pack_into("<I", raw, i+4, 0); break
        with self.assertRaisesRegex(NroError, "compute entry point"):
            reflect_compute(raw)


class GameShaderResultTests(unittest.TestCase):
    def run_probe(self, returncode, lines):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "static/MoltenVK.xcframework/macos-arm64_x86_64/libMoltenVK.a"
            library.parent.mkdir(parents=True); library.write_bytes(b"test-only")
            include = root / "include/vulkan/vulkan.h"
            include.parent.mkdir(parents=True); include.write_text("test-only")
            output = root / "out"; output.mkdir()

            def subprocess_stub(command, **kwargs):
                if command[0] == "xcrun": return subprocess.CompletedProcess(command, 0)
                kwargs["stdout"].write("\n".join(lines)+"\n")
                return subprocess.CompletedProcess(command, returncode)

            with patch("game_shader_smoke.platform.system", return_value="Darwin"), \
                 patch("game_shader_smoke.subprocess.run", side_effect=subprocess_stub):
                return run_native(root, output, root / "logs", {"verified": False})

    def successful_lines(self):
        return ["BIKO3_COMPUTE_RESULT " + json.dumps({"sha256": sha, "stage": "comp", "role": EXPECTED[sha]["role"],
            "entry_point": "main", "pipeline_created": True}) for sha in EXPECTED]

    def test_only_both_matching_original_pipeline_results_count_as_verified(self):
        self.assertTrue(self.run_probe(0, self.successful_lines())["verified"])
        with self.assertRaisesRegex(NroError, "both original compute pipelines"):
            self.run_probe(0, self.successful_lines()[:1])

    def test_partial_or_failed_compile_is_never_turned_into_skip(self):
        with self.assertRaisesRegex(NroError, "pipeline creation failed"):
            self.run_probe(1, self.successful_lines())
        with self.assertRaisesRegex(NroError, "no-GPU reason"):
            self.run_probe(77, [])
        with self.assertRaisesRegex(NroError, "no-GPU reason"):
            self.run_probe(77, ["BIKO3_COMPUTE_SKIP pipeline_compile_failed"])
        result = self.run_probe(77, ["BIKO3_COMPUTE_SKIP zero_physical_devices"])
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["verified"])


if __name__ == "__main__": unittest.main()
