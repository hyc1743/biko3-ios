import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from github_cloud import UploadProgress
from pack_resources import pack, MANIFEST
from publish_resources import resource_files, upload


class ResourcePublicationTests(unittest.TestCase):
    def test_only_matching_parts_within_output_directory_are_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "Data"
            data.mkdir()
            (data / "bk3_00.pp").write_bytes(b"fixture-resource")
            parts = root / "out/parts"
            pack(data, parts, part_bytes=8)
            embedded = root / "ios/resources" / MANIFEST
            embedded.parent.mkdir(parents=True)
            embedded.write_bytes((parts / MANIFEST).read_bytes())
            (parts / "prod.keys").write_bytes(b"must not be uploaded")
            with patch("publish_resources.ROOT", root):
                files = resource_files(parts)
                self.assertEqual([path.name for path, _ in files],
                                 ["Biko3Data.part001.b3data", "Biko3Data.part002.b3data"])
                with self.assertRaisesRegex(ValueError, "within workspace"):
                    resource_files(root / "Data")
                first = files[0][0]
                first.write_bytes(b"X" * first.stat().st_size)
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    resource_files(parts)

    def test_resume_skips_matching_remote_parts_and_verifies_new_digest(self):
        p1 = {"name": "first.b3data", "size": 8, "sha256": "a" * 64}
        p2 = {"name": "second.b3data", "size": 5, "sha256": "b" * 64}
        def remote(part):
            return {"name": part["name"], "size": part["size"], "state": "uploaded", "digest": "sha256:" + part["sha256"]}
        files = [(Path(p1["name"]), p1), (Path(p2["name"]), p2)]
        github = Mock()
        github.api.side_effect = [
            {"draft": False, "assets": [remote(p1)]}, remote(p2),
            {"assets": [remote(p1), remote(p2)]}]
        github.upload_release_asset.return_value = {"id": 123}
        result = upload(github, "owner/repo", 10, files)
        self.assertTrue(result["verified"])
        self.assertEqual(github.upload_release_asset.call_count, 1)
        self.assertEqual(github.upload_release_asset.call_args.args[-1], Path("second.b3data"))

    def test_conflicting_remote_asset_blocks_all_uploads(self):
        github = Mock()
        github.api.return_value = {"draft": False, "assets": [
            {"name": "first.b3data", "size": 8, "state": "uploaded", "digest": "sha256:wrong"}]}
        with self.assertRaisesRegex(ValueError, "does not match"):
            upload(github, "owner/repo", 10, [(Path("first.b3data"), {"size": 8, "sha256": "a" * 64})])
        github.upload_release_asset.assert_not_called()

    def test_progress_reader_bounds_memory_and_preserves_payload(self):
        data = b"x" * (2 * 1024 * 1024 + 1)
        progress = Mock()
        reader = UploadProgress(io.BytesIO(data), len(data), progress)
        chunks = []
        while chunk := reader.read():
            self.assertLessEqual(len(chunk), 1024 * 1024)
            chunks.append(chunk)
        self.assertEqual(b"".join(chunks), data)
        self.assertEqual(progress.call_args.args, (len(data), len(data)))


if __name__ == "__main__":
    unittest.main()
