"""Acquire analysis dependencies inside this workspace, respecting network policy.

No system installation, global environment changes, or MCP configuration edits.
Local ZIPs may be supplied when downloads are unavailable. Reference snapshots
contain source/documentation only, never game archives, ROMs, or key files.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REPOS = ("jhaizhou-ops/d2r-mobile", "seedds/nxnative", "LNPANDY/nxnative-romfs-archive",
         "ruje0504-2/biko3-switch-runtime")
SOURCE_SUFFIXES = {".md", ".txt", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp",
                   ".m", ".mm", ".swift", ".s", ".rs", ".py", ".sh", ".ps1", ".bat",
                   ".cmake", ".toml", ".json", ".yaml", ".yml", ".metal", ".glsl", ".vert",
                   ".frag", ".comp", ".plist", ".entitlements", ".xcconfig", ".pbxproj",
                   ".xcworkspacedata", ".in", ".def", ".inc", ".cs", ".java",
                   ".csproj", ".props", ".targets", ".sln", ".tsv", ".csv", ".lock",
                   ".cfg", ".ini", ".mk", ".ninja", ".gradle", ".properties", ".jsonl"}
SOURCE_NAMES = {"LICENSE", "COPYING", "Makefile", "CMakeLists.txt", ".gitmodules", ".gitignore"}


def source_filter() -> dict:
    # Record the full policy, so even a suffix-list edit invalidates old receipts.
    return {"version": 2, "suffixes": sorted(SOURCE_SUFFIXES), "names": sorted(SOURCE_NAMES),
            "max_file_bytes": 1024 * 1024, "max_total_bytes": 64 * 1024 * 1024,
            "regular_blobs_only": True}


def request(url: str):
    # Intentionally preserve the default proxy handling. Do not disable proxy
    # settings or certificate validation to evade an execution environment limit.
    headers = {"User-Agent": "biko3-ios-analysis/1", "Accept": "application/vnd.github+json"}
    # Hosted runners share anonymous API quotas. Scope the Actions token to the
    # API host only; raw sources and release redirects never receive it.
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    req = urllib.request.Request(url, headers=headers)
    parsed = urllib.parse.urlparse(url)
    if token and parsed.scheme == "https" and parsed.hostname == "api.github.com":
        # Unredirected headers are sent on this request only, never forwarded by
        # urllib's redirect handler to a raw source or release storage host.
        req.add_unredirected_header("Authorization", "Bearer " + token)
    return urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context())


def get_json(url: str):
    with request(url) as response:
        return json.load(response)


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


@contextmanager
def staging_directory(parent: Path, prefix: str):
    # Use inherited workspace ACLs. Python's mode-0700 Windows temporary
    # directories are inaccessible to this managed execution identity.
    parent = parent.resolve()
    if not parent.is_relative_to(ROOT):
        raise ValueError("staging directory must be inside this project")
    parent.mkdir(parents=True, exist_ok=True)
    stage = parent / (prefix + uuid.uuid4().hex)
    stage.mkdir()
    try:
        yield stage
    finally:
        if stage.exists():
            if not stage.resolve().is_relative_to(parent):
                raise ValueError("staging cleanup path left its parent")
            shutil.rmtree(stage)


def download(url: str, target: Path, expected: str | None = None) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and expected and file_digest(target) == expected:
        return {"path": str(target), "url": url, "sha256": expected, "expected_digest_verified": True}
    part = target.with_name(target.name + ".part")
    try:
        with request(url) as response, part.open("wb") as output:
            shutil.copyfileobj(response, output)
        observed = file_digest(part)
        if expected and observed.lower() != expected.lower():
            raise ValueError(f"checksum mismatch for {target.name}")
        part.replace(target)
        return {"path": str(target), "url": url, "sha256": observed,
                "expected_digest_verified": expected is not None}
    finally:
        part.unlink(missing_ok=True)


def unpack(archive: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # Validate every member before writing. Extract into a new staging directory,
    # so a failed extraction never masquerades as an installed dependency.
    if target.exists():
        raise ValueError(f"destination already exists: {target}; use its recorded installation")
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in entry.filename or ":" in entry.filename:
                raise ValueError(f"unsafe archive member: {entry.filename}")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"symlink archive member is unsupported: {entry.filename}")
        with staging_directory(target.parent, "unpack-") as stage:
            source.extractall(stage)
            Path(stage).rename(target)


def install_ghidra(args) -> dict:
    if platform.system() != "Windows" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("this local installer supports Windows x64; use a matching native Ghidra/JDK elsewhere")
    tools = ROOT / ".tools"
    manifest_path = tools / "analysis-toolchain.json"
    if manifest_path.exists():
        installed = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (Path(installed["ghidra_home"]) / "support/analyzeHeadless.bat").is_file() and (Path(installed["java_home"]) / "bin/java.exe").is_file():
            return installed
        raise ValueError("recorded toolchain is incomplete; inspect .tools/analysis-toolchain.json")
    records = []
    if args.ghidra_zip:
        archive = args.ghidra_zip.resolve()
        records.append({"path": str(archive), "sha256": file_digest(archive), "source": "user-supplied local archive"})
    else:
        releases = get_json("https://api.github.com/repos/NationalSecurityAgency/ghidra/releases?per_page=50")
        # The connected REA advertises the 12.1.x series. Resolve an actual
        # official release; never invent a release filename or a download mirror.
        release = next((r for r in releases if re.match(r"Ghidra_12\.1(?:\D|$)", r["tag_name"])
                        and not r["draft"] and not r["prerelease"]), None)
        if release is None:
            raise ValueError("official Ghidra 12.1.x release not found; supply --ghidra-zip explicitly")
        asset = next((a for a in release["assets"] if a["name"].endswith(".zip") and "PUBLIC" in a["name"]), None)
        if asset is None:
            raise ValueError("official Ghidra release has no PUBLIC ZIP asset")
        archive = tools / "cache" / asset["name"]
        digest = asset.get("digest") or ""
        expected = digest[7:] if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) else None
        records.append(download(asset["browser_download_url"], archive, expected))
    with zipfile.ZipFile(archive) as package:
        property_files = [n for n in package.namelist() if n.endswith("/Ghidra/application.properties")]
        if len(property_files) != 1:
            raise ValueError("ZIP does not contain one Ghidra application.properties")
        properties = package.read(property_files[0]).decode("utf-8")
        required_java = re.search(r"(?m)^application\.java\.min\s*=\s*(\d+)", properties)
        if not required_java:
            raise ValueError("cannot determine the JDK requirement from Ghidra application.properties")
        java_feature = int(required_java.group(1))
        ghidra_relative = PurePosixPath(property_files[0]).parents[1]
    if args.jdk_zip:
        java_archive = args.jdk_zip.resolve()
        records.append({"path": str(java_archive), "sha256": file_digest(java_archive), "source": "user-supplied local archive"})
    else:
        assets = get_json(f"https://api.adoptium.net/v3/assets/latest/{java_feature}/hotspot?architecture=x64&image_type=jdk&os=windows&vendor=eclipse")
        asset = next((a["binary"]["package"] for a in assets if a["binary"]["package"]["name"].endswith(".zip")), None)
        if asset is None or not re.fullmatch(r"[0-9a-fA-F]{64}", asset.get("checksum", "")):
            raise ValueError("official Temurin API did not provide a JDK ZIP with SHA-256")
        java_archive = tools / "cache" / asset["name"]
        records.append(download(asset["link"], java_archive, asset["checksum"]))
    # Check the Java archive layout before either installation is materialized.
    with zipfile.ZipFile(java_archive) as package:
        homes = [str(PurePosixPath(n).parents[1]) for n in package.namelist() if n.endswith("/bin/java.exe")]
        if len(homes) != 1:
            raise ValueError("JDK ZIP does not contain one Windows java.exe")
    ghidra_target, java_target = tools / "ghidra", tools / "jdk"
    unpack(archive, ghidra_target)
    unpack(java_archive, java_target)
    installed = {"ghidra_home": str(ghidra_target / str(ghidra_relative)),
                 "java_home": str(java_target / homes[0]), "required_java_feature": java_feature,
                 "downloads": records, "installed_at": datetime.now(timezone.utc).isoformat(),
                 "global_configuration_changed": False}
    manifest_path.write_text(json.dumps(installed, indent=2) + "\n", encoding="utf-8")
    return installed


def git_blob_digest(path: Path) -> str:
    digest = hashlib.sha1(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def acquire_source(repo: str, commit: str, destination: Path, entry: dict) -> dict:
    name = entry["path"]
    target = destination / name
    expected = entry["sha"]
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{urllib.parse.quote(name, safe='/')}"
    # Resume only complete files whose Git object ID matches the pinned tree.
    reused = target.is_file() and git_blob_digest(target) == expected
    if not reused:
        part = target.with_name(target.name + ".part")
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                with request(url) as response, part.open("wb") as output:
                    shutil.copyfileobj(response, output)
                if git_blob_digest(part) != expected:
                    raise ValueError(f"Git blob checksum mismatch: {repo}/{name}")
                part.replace(target)
                break
            except (OSError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
            finally:
                part.unlink(missing_ok=True)
    return {"path": str(target), "url": url, "sha256": file_digest(target),
            "git_blob_sha": expected, "git_blob_verified": True, "reused": reused}


def reference_commit(value: str) -> tuple[str, str]:
    repo, separator, sha = value.partition("=")
    if not separator or repo not in REPOS or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise argparse.ArgumentTypeError("expected a supported repository=40-hex-commit")
    return repo, sha.lower()


def acquire_reference(repo: str, commit_override: str | None = None) -> dict:
    destination = ROOT / "references" / repo.replace("/", "--")
    receipt = destination / "snapshot.json"
    recorded = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {}
    lock_path = ROOT / "references.lock.json"
    locked = json.loads(lock_path.read_text(encoding="utf-8"))["repositories"] if lock_path.exists() else {}
    # The project lock is authoritative; an explicit CLI pin can change it for
    # this run. Older unlisted snapshots retain their recorded commit.
    pinned = commit_override or locked.get(repo) or recorded.get("commit")
    if pinned is not None and not re.fullmatch(r"[0-9a-fA-F]{40}", pinned):
        raise ValueError(f"{repo}: reference commit must be 40 hexadecimal digits")
    if pinned:
        pinned = pinned.lower()
    if receipt.exists():
        if recorded.get("commit") == pinned and recorded.get("source_filter") == source_filter() and recorded.get("files") and all(
            Path(record["path"]).is_file()
            and Path(record["path"]).resolve().is_relative_to(destination.resolve())
            and git_blob_digest(Path(record["path"])) == record["git_blob_sha"]
            for record in recorded["files"]
        ):
            return recorded
        receipt.unlink()
    info = get_json(f"https://api.github.com/repos/{repo}")
    revision = urllib.parse.quote(pinned or info["default_branch"], safe="")
    commit = get_json(f"https://api.github.com/repos/{repo}/commits/{revision}")
    sha = commit["sha"]
    if pinned and sha != pinned:
        raise ValueError(f"{repo}: GitHub commit response does not match requested pin")
    tree = get_json(f"https://api.github.com/repos/{repo}/git/trees/{commit['commit']['tree']['sha']}?recursive=1")
    if tree.get("truncated"):
        raise ValueError(f"{repo}: recursive tree truncated; cannot claim a complete source snapshot")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "tree.json").write_text(json.dumps(tree, indent=2) + "\n", encoding="utf-8")
    selected, skipped = [], []
    total_bytes = 0
    for entry in tree["tree"]:
        name = PurePosixPath(entry["path"])
        wanted = name.suffix.lower() in SOURCE_SUFFIXES or name.name in SOURCE_NAMES
        # Mode 120000 denotes a Git symlink, not a regular source file.
        if entry["type"] != "blob" or entry.get("mode") == "120000" or not wanted or entry.get("size", 0) > 1024 * 1024:
            skipped.append(entry["path"])
            continue
        if name.is_absolute() or ".." in name.parts or "\\" in str(name) or ":" in str(name):
            raise ValueError("unsafe Git tree path")
        total_bytes += entry.get("size", 0)
        if total_bytes > 64 * 1024 * 1024:
            raise ValueError(f"{repo}: source snapshot exceeds 64 MiB; inspect tree.json before selective acquisition")
        selected.append(entry)
    # map preserves tree order while bounding active requests to eight.
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda entry: acquire_source(repo, sha, destination, entry), selected))
    # A deliberate version change must not leave source files from the previous
    # receipt mixed into the new tree. Remove only previously recorded files.
    selected_paths = {Path(record["path"]).resolve() for record in records}
    for old in recorded.get("files", []):
        path = Path(old["path"])
        if path.resolve().is_relative_to(destination.resolve()) and path.resolve() not in selected_paths:
            path.unlink(missing_ok=True)
    result = {"repository": repo, "commit": sha, "license": info.get("license"), "files": records,
              "source_filter": source_filter(), "skipped_paths": skipped,
              "coverage": "source/documentation/build metadata files <=1 MiB; binary assets and submodules excluded"}
    receipt_part = receipt.with_suffix(".json.part")
    receipt_part.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    receipt_part.replace(receipt)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("all", "ghidra", "references"), default="all")
    parser.add_argument("--ghidra-zip", type=Path)
    parser.add_argument("--jdk-zip", type=Path)
    parser.add_argument("--reference-commit", type=reference_commit, action="append", default=[],
                        metavar="REPOSITORY=SHA", help="explicitly override one locked reference commit")
    args = parser.parse_args()
    pins = dict(args.reference_commit)
    results = {"observed_at": datetime.now(timezone.utc).isoformat(), "steps": [], "complete": True}
    jobs = []
    if args.task in {"all", "ghidra"}:
        jobs.append(("ghidra-toolchain", lambda: install_ghidra(args)))
    if args.task in {"all", "references"}:
        jobs.extend((repo, lambda repo=repo: acquire_reference(repo, pins.get(repo))) for repo in REPOS)
    for name, job in jobs:
        print(f"Preparing {name}", flush=True)
        try:
            results["steps"].append({"name": name, "status": "complete", "result": job()})
        except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile, KeyError, StopIteration) as error:
            results["complete"] = False
            results["steps"].append({"name": name, "status": "blocked", "reason": str(error)})
            print(f"Blocked: {name}: {error}", file=sys.stderr, flush=True)
    output = ROOT / "work/biko3-ios/evidence/dependencies.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Dependency report: {output}")
    return 0 if results["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
