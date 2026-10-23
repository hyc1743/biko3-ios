import importlib.util
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "tools/bootstrap_analysis.py"
TEMP_ROOT = SCRIPT.parents[1] / "work/biko3-ios/test-temp"
spec = importlib.util.spec_from_file_location("bootstrap_analysis", SCRIPT)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class ArchiveTests(unittest.TestCase):
    def test_actions_api_token_is_never_sent_to_sources_or_redirects(self):
        with patch.dict(bootstrap.os.environ, {"GH_TOKEN": "test-only-token"}, clear=True), \
             patch.object(bootstrap.urllib.request, "urlopen") as open_url:
            bootstrap.request("https://api.github.com/repos/seedds/nxnative")
            request = open_url.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer test-only-token")
            redirect = bootstrap.urllib.request.HTTPRedirectHandler().redirect_request(
                request, None, 302, "Found", {}, "https://raw.githubusercontent.com/source")
            self.assertIsNone(redirect.get_header("Authorization"))
            for url in ("https://raw.githubusercontent.com/source", "https://github.com/release",
                        "https://api.github.com.example.org/source", "http://api.github.com/source"):
                bootstrap.request(url)
                self.assertIsNone(open_url.call_args.args[0].get_header("Authorization"))

    @classmethod
    def setUpClass(cls):
        if not TEMP_ROOT.resolve().is_relative_to(SCRIPT.parents[1]):
            raise RuntimeError("test temporary directory must stay inside the project")
        TEMP_ROOT.mkdir(parents=True, exist_ok=True)

    def test_extract_local_tool_archive(self):
        with bootstrap.staging_directory(TEMP_ROOT, "test-") as directory:
            root = Path(directory)
            archive, target = root / "tool.zip", root / "installed"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("ghidra/support/example.txt", "local test")
            bootstrap.unpack(archive, target)
            self.assertEqual((target / "ghidra/support/example.txt").read_text(), "local test")
            with self.assertRaisesRegex(ValueError, "already exists"):
                bootstrap.unpack(archive, target)

    def test_reject_escape_and_symlink_before_any_extraction(self):
        for name in ("../escaped.txt", "/absolute.txt", "C:/absolute.txt", "folder\\..\\escaped.txt", "symlink"):
            with self.subTest(name=name), bootstrap.staging_directory(TEMP_ROOT, "test-") as directory:
                root = Path(directory)
                archive, target = root / "bad.zip", root / "installed"
                with zipfile.ZipFile(archive, "w") as package:
                    package.writestr("normal.txt", "this must not be extracted")
                    info = zipfile.ZipInfo(name)
                    if name == "symlink":
                        info.create_system = 3
                        info.external_attr = 0o120777 << 16
                    package.writestr(info, "outside")
                with self.assertRaises(ValueError):
                    bootstrap.unpack(archive, target)
                self.assertFalse(target.exists())
                self.assertFalse((root / "escaped.txt").exists())


class ReferenceTests(unittest.TestCase):
    def fixture(self, files):
        entries = [{"path": name, "type": "blob", "mode": "100644", "size": len(data),
                    "sha": hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()}
                   for name, data in files.items()]
        return [{"default_branch": "main", "license": None},
                {"sha": "1" * 40, "commit": {"tree": {"sha": "tree"}}},
                {"tree": entries, "truncated": False}]

    def test_resume_verifies_existing_files_and_downloads_corrupt_file(self):
        files = {"good.cpp": b"good source", "changed.cpp": b"correct source", "asset.bin": b"excluded"}
        with bootstrap.staging_directory(TEMP_ROOT, "resume-") as root:
            destination = root / "references/test--repo"
            destination.mkdir(parents=True)
            (destination / "good.cpp").write_bytes(files["good.cpp"])
            (destination / "changed.cpp").write_bytes(b"untrusted partial")
            with patch.object(bootstrap, "ROOT", root), \
                 patch.object(bootstrap, "get_json", side_effect=self.fixture(files)), \
                 patch.object(bootstrap, "request", return_value=io.BytesIO(files["changed.cpp"])) as network:
                result = bootstrap.acquire_reference("test/repo")
            network.assert_called_once_with("https://raw.githubusercontent.com/test/repo/" + "1" * 40 + "/changed.cpp")
            self.assertTrue(result["files"][0]["reused"])
            self.assertFalse(result["files"][1]["reused"])
            self.assertTrue(all(record["git_blob_verified"] for record in result["files"]))
            self.assertEqual(result["skipped_paths"], ["asset.bin"])
            self.assertTrue((destination / "snapshot.json").is_file())
            # A completed receipt is verified again and can avoid all network I/O.
            with patch.object(bootstrap, "ROOT", root), patch.object(bootstrap, "get_json") as metadata:
                self.assertEqual(bootstrap.acquire_reference("test/repo"), result)
                metadata.assert_not_called()

    def test_checksum_failure_leaves_no_complete_receipt_and_retry_resumes(self):
        files = {"good.cpp": b"good source", "bad.cpp": b"expected source"}
        with bootstrap.staging_directory(TEMP_ROOT, "failed-") as root:
            destination = root / "references/test--repo"
            def response(url):
                return io.BytesIO(files["good.cpp"] if url.endswith("good.cpp") else b"bad response")
            with patch.object(bootstrap, "ROOT", root), \
                 patch.object(bootstrap, "get_json", side_effect=self.fixture(files)), \
                 patch.object(bootstrap, "request", side_effect=response):
                with self.assertRaisesRegex(ValueError, "Git blob checksum mismatch"):
                    bootstrap.acquire_reference("test/repo")
            self.assertFalse((destination / "snapshot.json").exists())
            self.assertFalse((destination / "bad.cpp").exists())
            self.assertFalse(list(destination.rglob("*.part")))
            self.assertEqual((destination / "good.cpp").read_bytes(), files["good.cpp"])
            with patch.object(bootstrap, "ROOT", root), \
                 patch.object(bootstrap, "get_json", side_effect=self.fixture(files)), \
                 patch.object(bootstrap, "request", return_value=io.BytesIO(files["bad.cpp"])) as network:
                bootstrap.acquire_reference("test/repo")
            self.assertEqual(network.call_count, 1)
            self.assertTrue((destination / "snapshot.json").is_file())

    def test_network_failure_does_not_write_receipt(self):
        with bootstrap.staging_directory(TEMP_ROOT, "network-") as root:
            with patch.object(bootstrap, "ROOT", root), \
                 patch.object(bootstrap, "get_json", side_effect=self.fixture({"source.cpp": b"source"})), \
                 patch.object(bootstrap, "request", side_effect=OSError("offline")) as network, \
                 patch.object(bootstrap.time, "sleep"):
                with self.assertRaisesRegex(OSError, "offline"):
                    bootstrap.acquire_reference("test/repo")
            self.assertEqual(network.call_count, 3)
            self.assertFalse((root / "references/test--repo/snapshot.json").exists())

    def test_corrupted_completed_snapshot_loses_receipt_until_repaired(self):
        files = {"source.cpp": b"correct source"}
        with bootstrap.staging_directory(TEMP_ROOT, "corrupt-") as root:
            destination = root / "references/test--repo"
            destination.mkdir(parents=True)
            source = destination / "source.cpp"
            source.write_bytes(b"modified after download")
            receipt = destination / "snapshot.json"
            receipt.write_text(json.dumps({"source_filter": bootstrap.source_filter(), "commit": "1" * 40,
                "files": [{"path": str(source),
                "git_blob_sha": self.fixture(files)[2]["tree"][0]["sha"]}]}))
            with patch.object(bootstrap, "ROOT", root), \
                 patch.object(bootstrap, "get_json", side_effect=self.fixture(files)), \
                 patch.object(bootstrap, "request", return_value=io.BytesIO(b"invalid replacement")):
                with self.assertRaisesRegex(ValueError, "Git blob checksum mismatch"):
                    bootstrap.acquire_reference("test/repo")
            self.assertFalse(receipt.exists())

    def test_filter_upgrade_fetches_build_metadata_missing_from_old_receipt(self):
        files = {"source.cpp": b"source", "game.tsv": b"address\tname\n123\tmain\n",
                 "packages.lock": b"locked dependency", "samples.csv": b"name,value\n",
                 "Emitter.cs": b"class Emitter {}", "game.nsp": b"binary game asset",
                 "image.png": b"binary image"}
        with bootstrap.staging_directory(TEMP_ROOT, "upgrade-") as root:
            destination = root / "references/test--repo"
            destination.mkdir(parents=True)
            source = destination / "source.cpp"
            source.write_bytes(files["source.cpp"])
            old_policy = bootstrap.source_filter()
            old_policy["suffixes"] = [".cpp"]
            (destination / "snapshot.json").write_text(json.dumps({"source_filter": old_policy, "commit": "1" * 40,
                "files": [{"path": str(source),
                           "git_blob_sha": self.fixture(files)[2]["tree"][0]["sha"]}]}))
            def response(url):
                return io.BytesIO(files[url.rsplit("/", 1)[1]])
            tree = self.fixture(files)
            tree[2]["tree"].extend([
                {"path": "external.json", "type": "commit", "mode": "160000"},
                {"path": "link.tsv", "type": "blob", "mode": "120000", "size": 0}])
            with patch.object(bootstrap, "ROOT", root), \
                 patch.object(bootstrap, "get_json", side_effect=tree) as metadata, \
                 patch.object(bootstrap, "request", side_effect=response) as network:
                result = bootstrap.acquire_reference("test/repo")
            self.assertEqual(network.call_count, 4)
            self.assertEqual(result["source_filter"], bootstrap.source_filter())
            self.assertEqual({Path(record["path"]).name for record in result["files"]},
                             {"source.cpp", "game.tsv", "packages.lock", "samples.csv", "Emitter.cs"})
            self.assertEqual(set(result["skipped_paths"]),
                             {"game.nsp", "image.png", "external.json", "link.tsv"})
            self.assertTrue(result["files"][0]["reused"])
            self.assertEqual(result["commit"], "1" * 40)
            self.assertEqual(metadata.call_args_list[1].args[0],
                             "https://api.github.com/repos/test/repo/commits/" + "1" * 40)

    def test_explicit_commit_overrides_valid_receipt_and_removes_obsolete_files(self):
        with bootstrap.staging_directory(TEMP_ROOT, "repin-") as root:
            with patch.object(bootstrap, "ROOT", root), \
                 patch.object(bootstrap, "get_json", side_effect=self.fixture({"old.cpp": b"old"})), \
                 patch.object(bootstrap, "request", return_value=io.BytesIO(b"old")):
                bootstrap.acquire_reference("test/repo")
            new_fixture = self.fixture({"new.cpp": b"new"})
            new_fixture[1]["sha"] = "2" * 40
            with patch.object(bootstrap, "ROOT", root), \
                 patch.object(bootstrap, "get_json", side_effect=new_fixture) as metadata, \
                 patch.object(bootstrap, "request", return_value=io.BytesIO(b"new")):
                result = bootstrap.acquire_reference("test/repo", "2" * 40)
            self.assertEqual(result["commit"], "2" * 40)
            self.assertEqual(metadata.call_args_list[1].args[0],
                             "https://api.github.com/repos/test/repo/commits/" + "2" * 40)
            self.assertFalse((root / "references/test--repo/old.cpp").exists())

    def test_first_acquisition_uses_project_lock(self):
        with bootstrap.staging_directory(TEMP_ROOT, "locked-") as root:
            (root / "references.lock.json").write_text(json.dumps({"repositories": {"test/repo": "3" * 40}}))
            fixture = self.fixture({"source.cpp": b"source"})
            fixture[1]["sha"] = "3" * 40
            with patch.object(bootstrap, "ROOT", root), \
                 patch.object(bootstrap, "get_json", side_effect=fixture) as metadata, \
                 patch.object(bootstrap, "request", return_value=io.BytesIO(b"source")):
                result = bootstrap.acquire_reference("test/repo")
            self.assertEqual(result["commit"], "3" * 40)
            self.assertTrue(metadata.call_args_list[1].args[0].endswith("3" * 40))

    def test_explicit_pin_requires_supported_repo_and_full_sha(self):
        self.assertEqual(bootstrap.reference_commit("seedds/nxnative=" + "A" * 40),
                         ("seedds/nxnative", "a" * 40))
        for value in ("seedds/nxnative=main", "seedds/nxnative=123", "unknown/repo=" + "a" * 40):
            with self.assertRaises(bootstrap.argparse.ArgumentTypeError):
                bootstrap.reference_commit(value)


if __name__ == "__main__":
    unittest.main()
