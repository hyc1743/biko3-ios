import json
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from github_cloud import GitHub, SafeRedirect, credential, publication_files


class CloudTests(unittest.TestCase):
    def test_credential_uses_noninteractive_capture_and_never_commandline_token(self):
        result = subprocess.CompletedProcess([], 0, "username=test\npassword=secret-value\n", "")
        with patch.dict(os.environ, {}, clear=True), patch("github_cloud.git_executable", return_value="git"), patch("github_cloud.subprocess.run", return_value=result) as run:
            self.assertEqual(credential("existing-checkout"), "secret-value")
        args, kwargs = run.call_args
        self.assertNotIn("secret-value", str(args))
        self.assertEqual(kwargs["env"]["GCM_INTERACTIVE"], "Never")
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(kwargs["cwd"], "existing-checkout")
        self.assertTrue(kwargs["capture_output"])

    def test_credential_failure_does_not_expose_helper_output(self):
        result = subprocess.CompletedProcess([], 1, "password=do-not-show", "secret-helper-error")
        with patch.dict(os.environ, {}, clear=True), patch("github_cloud.git_executable", return_value="git"), patch("github_cloud.subprocess.run", return_value=result):
            with self.assertRaises(ValueError) as error:
                credential()
        self.assertNotIn("do-not-show", str(error.exception))
        self.assertNotIn("secret-helper-error", str(error.exception))

    def test_cross_host_download_does_not_leak_bearer_token(self):
        request = urllib.request.Request("https://api.github.com/repos/o/r/actions/artifacts/1/zip", headers={"Authorization": "Bearer sensitive"})
        redirect = SafeRedirect().redirect_request(request, None, 302, "Found", {}, "https://storage.example/artifact.zip")
        self.assertFalse(redirect.has_header("Authorization"))
        self.assertTrue(request.has_header("Authorization"))
        with self.assertRaises(ValueError):
            SafeRedirect().redirect_request(request, None, 302, "Found", {}, "http://storage.example/artifact.zip")

    def test_review_requires_exact_safe_files_and_hashes_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("Public source\n")
            manifest = root / "manifest.json"
            manifest.write_text('["README.md"]')
            files = publication_files(root, manifest)
            self.assertEqual(files[0][0], "README.md")
            self.assertEqual(len(files[0][2]), 64)
            for entry in ("../escape.py", "C:/keys", "work/report.md", "prod.keys", "game.nro", "README.md/", "./README.md", "README.md\\x"):
                manifest.write_text(json.dumps([entry]))
                with self.subTest(entry=entry), self.assertRaises(ValueError):
                    publication_files(root, manifest)
            manifest.write_text('["README.md", "README.md"]')
            with self.assertRaises(ValueError):
                publication_files(root, manifest)

    def test_review_rejects_disguised_binary_or_private_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text('["innocent.py"]')
            for data in (b"game\0data", b"-----BEGIN PRIVATE KEY-----\nsecret"):
                (root / "innocent.py").write_bytes(data)
                with self.assertRaises(ValueError):
                    publication_files(root, manifest)

    def test_only_exact_supported_encrypted_guest_path_is_exempt_from_binary_rule(self):
        import ci_guest
        plain = b"\0" * 16 + b"NRO0" + b"sample" * 40
        with patch.object(ci_guest, "PINNED_SHA256", hashlib.sha256(plain).hexdigest()):
            encrypted = ci_guest.seal(plain, bytes(range(32)))
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "ci-inputs").mkdir()
                manifest = root / "manifest.json"
                manifest.write_text('["ci-inputs/guest.enc"]')
                guest = root / "ci-inputs/guest.enc"
                guest.write_bytes(encrypted)
                self.assertEqual(publication_files(root, manifest)[0][0], "ci-inputs/guest.enc")
                guest.write_bytes(b"fake encrypted bytes")
                with self.assertRaises(ValueError):
                    publication_files(root, manifest)
                (root / "ci-inputs/other.enc").write_bytes(encrypted)
                manifest.write_text('["ci-inputs/other.enc"]')
                with self.assertRaises(ValueError):
                    publication_files(root, manifest)

    def test_create_and_dispatch_use_correct_explicit_api_requests(self):
        github = GitHub("secret")
        with patch.object(github, "api", return_value={}) as api:
            github.create_public("biko3-ios", "iOS port")
            self.assertEqual(api.call_args.args, ("POST", "/user/repos", {"name": "biko3-ios", "description": "iOS port", "private": False, "auto_init": False}))
            github.dispatch("owner/biko3-ios", "build.yml", "main", {"mode": "shell"})
            self.assertEqual(api.call_args.args, ("POST", "/repos/owner/biko3-ios/actions/workflows/build.yml/dispatches", {"ref": "main", "inputs": {"mode": "shell"}}))

    def test_actions_secret_is_sealed_to_repository_public_key(self):
        from nacl.public import PrivateKey, SealedBox
        private = PrivateKey.generate()
        github = GitHub("auth-token")
        response = {"key_id": "1234", "key": base64.b64encode(bytes(private.public_key)).decode()}
        with patch.object(github, "api", side_effect=[response, None]) as api:
            github.set_secret("owner/biko3-ios", "BIKO3_GUEST_KEY", b"private-secret")
        args = api.call_args.args
        self.assertEqual(args[:2], ("PUT", "/repos/owner/biko3-ios/actions/secrets/BIKO3_GUEST_KEY"))
        self.assertNotIn("private-secret", str(args))
        self.assertEqual(SealedBox(private).decrypt(base64.b64decode(args[2]["encrypted_value"])), b"private-secret")


if __name__ == "__main__":
    unittest.main()
