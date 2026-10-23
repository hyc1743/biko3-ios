#!/usr/bin/env python3
"""GitHub cloud transport. Credentials stay in memory; publication is explicit.

Use --credential-dir with an existing authenticated checkout when its local
credential configuration is needed. Source publication permits only the pinned
encrypted guest input. Development IPA releases require a matching build report.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APP_ICON_PATH = "ios/Assets.xcassets/AppIcon.appiconset/AppIcon.png"
# Exact user-supplied artwork resized to an opaque 1024px app icon.
APP_ICON_SHA256 = "fc52f433c0ab7b0d537f3bae46a9b4c2887212e0246b71ad913e39440c084c47"
LOCALIZATION_PATCH_PATH = "ios/localization/patch.pp"
LOCALIZATION_PATCH_SHA256 = "c01589312262a1faa4bce91698d2721538f21a1e830a98c42e4cd5aafedd4b8e"


def git_executable():
    found = shutil.which("git")
    fallback = Path("C:/Program Files/Git/cmd/git.exe")
    if found:
        return found
    if fallback.is_file():
        return str(fallback)
    raise ValueError("Git is not installed")


def credential(directory=None):
    value = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if value:
        return value.strip()
    result = subprocess.run(
        [git_executable(), "credential", "fill"], cwd=directory,
        input="protocol=https\nhost=github.com\n\n", capture_output=True,
        text=True, timeout=30,
        env=dict(os.environ, GCM_INTERACTIVE="Never", GIT_TERMINAL_PROMPT="0"))
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or not fields.get("password"):
        raise ValueError("No noninteractive GitHub credential; use GH_TOKEN or --credential-dir")
    return fields["password"]


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("Refusing insecure download redirect")
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlsplit(request.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


class UploadRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Asset uploads are pinned to the documented GitHub upload host.
        # Reject redirects entirely rather than risking forwarding a token/body.
        raise ValueError("GitHub release upload redirected unexpectedly")


class UploadProgress:
    """A bounded reader; credentials and content never enter progress output."""
    def __init__(self, source, total, callback):
        self.source, self.total, self.callback = source, total, callback
        self.done, self.last = 0, 0.0

    def read(self, size=-1):
        data = self.source.read(min(size, 1024 * 1024) if size >= 0 else 1024 * 1024)
        self.done += len(data)
        now = time.monotonic()
        if now - self.last >= 5 or self.done == self.total:
            self.last = now
            self.callback(self.done, self.total)
        return data


def repository(value):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("Expected owner/repository")
    return value


class GitHub:
    def __init__(self, token):
        self.token = token
        self.opener = urllib.request.build_opener(SafeRedirect())

    def request(self, method, path, payload=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Expected GitHub API path")
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request("https://api.github.com" + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                     "User-Agent": "Biko3-cloud", "X-GitHub-Api-Version": "2022-11-28",
                     "Content-Type": "application/json"})
        return self.opener.open(request, timeout=60)

    def api(self, method, path, payload=None):
        with self.request(method, path, payload) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def identity(self):
        result = self.api("GET", "/user")
        return {key: result[key] for key in ("login", "id", "html_url")}

    def create_public(self, name, description):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("Invalid repository name")
        return self.api("POST", "/user/repos", {"name": name, "description": description,
                                                "private": False, "auto_init": False})

    def set_secret(self, repo, name, value):
        from nacl.public import PublicKey, SealedBox
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
            raise ValueError("Invalid Actions secret name")
        path = "/repos/" + repository(repo) + "/actions/secrets"
        public = self.api("GET", path + "/public-key")
        encrypted = SealedBox(PublicKey(base64.b64decode(public["key"]))).encrypt(value)
        self.api("PUT", path + "/" + name,
                 {"encrypted_value": base64.b64encode(encrypted).decode(), "key_id": public["key_id"]})

    def create_release(self, repo, tag, target_sha, body, *, stable=False):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", tag) or not re.fullmatch(r"[0-9a-fA-F]{40}", target_sha):
            raise ValueError("Release requires a simple tag and exact 40-character commit SHA")
        if not body.strip():
            raise ValueError("Release requires reviewed Markdown text")
        return self.api("POST", "/repos/" + repository(repo) + "/releases",
                        {"tag_name": tag, "target_commitish": target_sha, "name": tag,
                         "body": body, "draft": False, "prerelease": not stable,
                         "make_latest": "true" if stable else "false"})

    def upload_release_asset(self, repo, release_id, path, *, progress=None):
        path = Path(path)
        url = f"https://uploads.github.com/repos/{repository(repo)}/releases/{int(release_id)}/assets?"
        url += urllib.parse.urlencode({"name": path.name})
        headers = {"Authorization": "Bearer " + self.token, "User-Agent": "Biko3-cloud",
                   "Accept": "application/vnd.github+json", "Content-Type": "application/octet-stream",
                   "Content-Length": str(path.stat().st_size), "X-GitHub-Api-Version": "2022-11-28"}
        opener = urllib.request.build_opener(UploadRedirect())
        with path.open("rb") as source:
            body = UploadProgress(source, path.stat().st_size, progress) if progress else source
            request = urllib.request.Request(url, data=body, method="POST", headers=headers)
            with opener.open(request, timeout=180) as response:
                result = json.load(response)
        if result.get("name") != path.name or result.get("size") != path.stat().st_size or result.get("state") != "uploaded":
            raise ValueError("GitHub release asset upload was incomplete")
        return {key: result[key] for key in ("id", "name", "size", "browser_download_url")}

    def upload_release_assets(self, repo, release_id, paths, *, stable=False):
        files = release_files(paths)
        release = self.api("GET", f"/repos/{repository(repo)}/releases/{int(release_id)}")
        if release.get("prerelease") is not (not stable) or release.get("draft") is not False:
            raise ValueError("Release type must match the explicitly selected stable/prerelease mode")
        existing = {item["name"] for item in release.get("assets", [])}
        if existing.intersection(path.name for path in files):
            raise ValueError("Release already contains an asset with a requested name")
        return [self.upload_release_asset(repo, release_id, path) for path in files]

    def dispatch(self, repo, workflow, ref, inputs):
        path = "/repos/" + repository(repo) + "/actions/workflows/" + urllib.parse.quote(workflow, safe="")
        self.api("POST", path + "/dispatches", {"ref": ref, "inputs": inputs})

    def run(self, repo, run_id):
        return self.api("GET", f"/repos/{repository(repo)}/actions/runs/{int(run_id)}")

    def runs(self, repo, workflow, branch):
        path = "/repos/" + repository(repo) + "/actions/workflows/" + urllib.parse.quote(workflow, safe="")
        query = urllib.parse.urlencode({"branch": branch, "per_page": 20})
        return self.api("GET", path + "/runs?" + query)["workflow_runs"]

    def artifacts(self, repo, run_id):
        result, page = [], 1
        while True:
            batch = self.api("GET", f"/repos/{repository(repo)}/actions/runs/{int(run_id)}/artifacts?per_page=100&page={page}")["artifacts"]
            result.extend(batch)
            if len(batch) < 100:
                return result
            page += 1

    def download_artifact(self, repo, artifact_id, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".partial")
        try:
            with self.request("GET", f"/repos/{repository(repo)}/actions/artifacts/{int(artifact_id)}/zip") as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


DENIED_PARTS = {".git", ".tools", "references", "switch", "work", "__pycache__"}
DENIED_SUFFIXES = {".keys", ".nsp", ".nro", ".nso", ".elf", ".ipa", ".zip", ".7z", ".bin", ".spv", ".metallib", ".pyc"}


def release_files(paths, *, release_root=None):
    """Only reviewed development IPA/report assets from the release directory."""
    root = (Path(release_root) if release_root is not None else ROOT / "artifacts/release").resolve()
    files, names, reports, ipas = [], set(), [], []
    for value in paths:
        path = Path(value)
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or not path.is_file() or any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError("Release asset must be a regular file inside artifacts/release")
        if path.suffix.lower() not in {".ipa", ".json", ".md"} or path.name in names:
            raise ValueError("Release assets allow unique .ipa, .json and .md files only")
        if path.suffix.lower() == ".json":
            report = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(report, dict) or report.get("contains_guest_executable") is not True or
                    report.get("signed") is not False or report.get("runtime_verified") is not False):
                raise ValueError("Release report must identify an unsigned, unverified real guest IPA")
            reports.append(report)
        elif path.suffix.lower() == ".ipa":
            if not zipfile.is_zipfile(path):
                raise ValueError("Release IPA is not a ZIP application package")
            with zipfile.ZipFile(path) as archive:
                entries = archive.namelist()
                if not {"Payload/Biko3.app/Biko3", "Payload/Biko3.app/Info.plist"}.issubset(entries):
                    raise ValueError("Release IPA is missing the Biko3 application")
                for name in entries:
                    parts = PurePosixPath(name)
                    if (parts.is_absolute() or ".." in parts.parts or "data" in {p.lower() for p in parts.parts} or
                            parts.suffix.lower() in {".keys", ".nsp", ".nro", ".nso"}):
                        raise ValueError("Release IPA contains original inputs or unsafe paths")
            ipas.append(path)
        else:
            content = path.read_bytes()
            if (len(content) > 5 * 1024 * 1024 or b"\0" in content or
                    re.search(rb"(?m)^-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----\r?$", content)):
                raise ValueError("Release Markdown must be plain text without private keys")
            try:
                content.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("Release Markdown must be UTF-8 text") from None
        names.add(path.name)
        files.append(resolved)
    if not ipas or not reports:
        raise ValueError("Release upload requires both an IPA and its build report")
    for ipa in ipas:
        with ipa.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if not any(r.get("ipa_sha256") == digest and r.get("ipa_bytes") == ipa.stat().st_size for r in reports):
            raise ValueError("Release IPA does not match a reviewed build report")
    return files


def publication_files(root, manifest):
    """Resolve exact reviewed files, refusing generated/private inputs and links."""
    root = Path(root).resolve()
    entries = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("Publication manifest must be a nonempty JSON list of exact paths")
    result, seen = [], set()
    for entry in entries:
        if not isinstance(entry, str) or "\\" in entry or ":" in entry:
            raise ValueError("Manifest paths must be relative POSIX paths")
        rel = PurePosixPath(entry)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts or entry != rel.as_posix() or entry in seen:
            raise ValueError("Unsafe or repeated publication path")
        if any(p.lower() in DENIED_PARTS for p in rel.parts) or rel.suffix.lower() in DENIED_SUFFIXES:
            raise ValueError("Private/generated file is in publication manifest: " + entry)
        path = root.joinpath(*rel.parts)
        if any(p.is_symlink() for p in [path, *path.parents]) or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("Publication entry must be a regular file within the workspace: " + entry)
        data = path.read_bytes()
        if entry == "ci-inputs/guest.enc":
            from ci_guest import inspect_container
            inspect_container(data)
        elif entry == APP_ICON_PATH:
            if hashlib.sha256(data).hexdigest() != APP_ICON_SHA256:
                raise ValueError("App icon differs from the reviewed user-supplied artwork")
        elif entry == LOCALIZATION_PATCH_PATH:
            if hashlib.sha256(data).hexdigest() != LOCALIZATION_PATCH_SHA256:
                raise ValueError("Localization patch differs from the reviewed candidate")
        elif (len(data) > 5 * 1024 * 1024 or b"\0" in data or
              re.search(rb"(?m)^-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----\r?$", data)):
            raise ValueError("Publication entry is binary, oversized or contains a private key: " + entry)
        result.append((entry, path, hashlib.sha256(data).hexdigest()))
        seen.add(entry)
    return result


def publish(github, repo, root, manifest, branch="main", message="Publish reviewed iOS port source"):
    """Publish an isolated commit containing only explicitly reviewed files."""
    repository(repo)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", branch):
        raise ValueError("Invalid branch")
    files = publication_files(root, manifest)
    remote = github.api("GET", "/repos/" + repo)
    if remote["private"]:
        raise ValueError("Expected the requested public source repository")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    authorization = base64.b64encode(("x-access-token:" + github.token).encode()).decode()
    env.update(GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
               GIT_CONFIG_VALUE_0="Authorization: Basic " + authorization,
               GIT_CONFIG_KEY_1="credential.helper", GIT_CONFIG_VALUE_1="")
    def git(*args, cwd):
        result = subprocess.run([git_executable(), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            # Never include captured output: HTTP traces or helpers can contain credentials.
            raise ValueError("Git publication command failed (exit " + str(result.returncode) + ")")
        return result.stdout
    with tempfile.TemporaryDirectory(prefix="biko3-public-") as directory:
        git("init", "-b", branch, cwd=directory)
        git("remote", "add", "origin", "https://github.com/" + repo + ".git", cwd=directory)
        # A normal fast-forward update preserves history and never force pushes.
        refs = git("ls-remote", "--heads", "origin", branch, cwd=directory)
        if refs.strip():
            git("fetch", "--depth=1", "origin", branch, cwd=directory)
            git("reset", "--soft", "FETCH_HEAD", cwd=directory)
        for entry, source, expected in files:
            destination = Path(directory).joinpath(*PurePosixPath(entry).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Publication file changed after review: " + entry)
            destination.write_bytes(data)
        git("add", "--all", cwd=directory)
        actual = set(git("ls-files", "-z", cwd=directory).strip("\0").split("\0"))
        if actual != {entry for entry, _, _ in files}:
            raise ValueError("Git staging differs from the reviewed publication manifest")
        git("-c", "user.name=Biko3 cloud", "-c", "user.email=biko3-cloud@users.noreply.github.com",
            "commit", "--allow-empty", "-m", message, cwd=directory)
        commit = git("rev-parse", "HEAD", cwd=directory).strip()
        git("push", "origin", "HEAD:refs/heads/" + branch, cwd=directory)
    return {"repository": repo, "commit": commit, "files": len(files)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-dir", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("identity")
    secret = commands.add_parser("set-secret")
    secret.add_argument("repo")
    secret.add_argument("name")
    secret.add_argument("--value-file", type=Path, help="Read private file; omit to read stdin")
    create = commands.add_parser("create-public")
    create.add_argument("name")
    create.add_argument("--description", default="Biko3 iOS port source and cloud build")
    release = commands.add_parser("release")
    release.add_argument("repo")
    release.add_argument("tag")
    release.add_argument("--target-sha", required=True)
    release.add_argument("--body-file", type=Path, required=True)
    release.add_argument("--stable", action="store_true", help="Explicitly publish a regular release as latest")
    assets = commands.add_parser("upload-release-assets")
    assets.add_argument("repo")
    assets.add_argument("release_id", type=int)
    assets.add_argument("files", nargs="+", type=Path)
    assets.add_argument("--stable", action="store_true", help="Explicitly allow assets on a regular release")
    review = commands.add_parser("review")
    review.add_argument("--root", type=Path, default=Path.cwd())
    review.add_argument("--manifest", type=Path, required=True)
    push = commands.add_parser("publish")
    push.add_argument("repo")
    push.add_argument("--root", type=Path, default=Path.cwd())
    push.add_argument("--manifest", type=Path, required=True)
    push.add_argument("--branch", default="main")
    push.add_argument("--message", default="Publish reviewed iOS port source")
    dispatch = commands.add_parser("dispatch")
    dispatch.add_argument("repo")
    dispatch.add_argument("workflow")
    dispatch.add_argument("--ref", default="main")
    dispatch.add_argument("--inputs", default="{}", help="JSON object")
    for name in ("run", "artifacts"):
        command = commands.add_parser(name)
        command.add_argument("repo")
        command.add_argument("run_id", type=int)
    runs = commands.add_parser("runs")
    runs.add_argument("repo")
    runs.add_argument("workflow")
    runs.add_argument("--branch", default="main")
    download = commands.add_parser("download")
    download.add_argument("repo")
    download.add_argument("artifact_id", type=int)
    download.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    if args.command == "review":
        result = [{"path": name, "sha256": digest, "bytes": path.stat().st_size}
                  for name, path, digest in publication_files(args.root, args.manifest)]
    else:
        github = GitHub(credential(args.credential_dir))
        if args.command == "identity":
            result = github.identity()
        elif args.command == "set-secret":
            if args.value_file:
                private_root = Path(".tools/private").resolve()
                source = args.value_file.resolve()
                if not source.is_relative_to(private_root) or source.suffix != ".key" or args.value_file.is_symlink():
                    raise ValueError("Secret file must be a .key within project .tools/private")
                value = source.read_bytes().strip()
            else:
                value = sys.stdin.buffer.read().strip()
            if not value:
                raise ValueError("Empty secret")
            github.set_secret(args.repo, args.name, value)
            result = {"secret_set": args.name, "repository": args.repo}
        elif args.command == "create-public":
            response = github.create_public(args.name, args.description)
            result = {key: response[key] for key in ("full_name", "html_url", "private")}
        elif args.command == "release":
            response = github.create_release(args.repo, args.tag, args.target_sha, args.body_file.read_text(encoding="utf-8"), stable=args.stable)
            result = {key: response[key] for key in ("id", "tag_name", "html_url", "prerelease", "target_commitish")}
        elif args.command == "upload-release-assets":
            result = github.upload_release_assets(args.repo, args.release_id, args.files, stable=args.stable)
        elif args.command == "publish":
            result = publish(github, args.repo, args.root, args.manifest, args.branch, args.message)
        elif args.command == "dispatch":
            inputs = json.loads(args.inputs)
            if not isinstance(inputs, dict):
                raise ValueError("Workflow inputs must be a JSON object")
            github.dispatch(args.repo, args.workflow, args.ref, inputs)
            result = {"dispatched": True, "repo": args.repo, "workflow": args.workflow, "ref": args.ref}
        elif args.command == "run":
            response = github.run(args.repo, args.run_id)
            result = {key: response.get(key) for key in ("id", "status", "conclusion", "html_url", "head_sha")}
        elif args.command == "runs":
            result = [{key: item.get(key) for key in ("id", "status", "conclusion", "html_url", "head_sha", "created_at")}
                      for item in github.runs(args.repo, args.workflow, args.branch)]
        elif args.command == "artifacts":
            result = [{key: item[key] for key in ("id", "name", "size_in_bytes", "expired")}
                      for item in github.artifacts(args.repo, args.run_id)]
        else:
            github.download_artifact(args.repo, args.artifact_id, args.destination)
            result = {"downloaded": str(args.destination)}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        print("GitHub API failed: HTTP " + str(error.code), file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        # Do not dump requests, credential subprocess output, or authentication headers.
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        sys.exit(1)
