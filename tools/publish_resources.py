#!/usr/bin/env python3
"""Publish only manifest-bound resource parts explicitly requested by the user."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.error

from github_cloud import GitHub, credential, repository
from pack_resources import read_manifest, checked_stat

ROOT = Path(__file__).resolve().parents[1]


def resource_files(directory):
    directory = Path(directory).absolute()
    if not directory.resolve().is_relative_to((ROOT / "out").resolve()):
        raise ValueError("Resource upload directory must be within workspace out/")
    for parent in (directory, *directory.parents):
        if parent.is_symlink():
            raise ValueError("Resource upload directory cannot contain links")
    checked_stat(directory, directory=True)
    embedded = ROOT / "ios/resources/Biko3Data.manifest.json"
    manifest = read_manifest(embedded)
    if (directory / "Biko3Data.manifest.json").read_bytes() != embedded.read_bytes():
        raise ValueError("Upload manifest differs from the IPA resource manifest")
    files = []
    for part in manifest["parts"]:
        path = directory / part["name"]
        before = checked_stat(path)
        if before.st_size != part["size"]:
            raise ValueError("Resource part size mismatch: " + path.name)
        with path.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        after = checked_stat(path)
        if actual != part["sha256"] or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("Resource part content changed or SHA256 mismatch: " + path.name)
        files.append((path, part))
    return files


def matches(asset, part):
    return (asset.get("state") == "uploaded" and asset.get("size") == part["size"] and
            asset.get("digest") == "sha256:" + part["sha256"])


def upload(github, repo, release_id, files):
    base = f"/repos/{repository(repo)}/releases/{int(release_id)}"
    release = github.api("GET", base)
    if release.get("draft") is not False:
        raise ValueError("Expected an existing published release")
    existing = {asset["name"]: asset for asset in release.get("assets", [])}
    # Check all collisions before the first upload; never replace existing data.
    for path, part in files:
        if path.name in existing and not matches(existing[path.name], part):
            raise ValueError("Existing release asset does not match: " + path.name)
    for path, part in files:
        if path.name in existing:
            print(json.dumps({"already_verified": path.name}), flush=True)
            continue
        print(json.dumps({"upload_start": path.name, "bytes": part["size"]}), flush=True)
        asset = github.upload_release_asset(repo, release_id, path,
            progress=lambda done, total: print(json.dumps({"uploading": path.name,
                "bytes_sent": done, "total_bytes": total}), flush=True))
        remote = github.api("GET", f"/repos/{repository(repo)}/releases/assets/{asset['id']}")
        if not matches(remote, part):
            raise ValueError("Uploaded resource digest does not match: " + path.name)
        print(json.dumps({"uploaded_verified": path.name, "id": asset["id"]}), flush=True)
    final = github.api("GET", base)
    assets = {asset["name"]: asset for asset in final.get("assets", [])}
    if any(path.name not in assets or not matches(assets[path.name], part) for path, part in files):
        raise ValueError("Final release resource inventory is incomplete")
    return {"verified": True, "release_id": release_id, "resource_parts": len(files),
            "total_bytes": sum(part["size"] for _, part in files)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo")
    parser.add_argument("release_id", type=int)
    parser.add_argument("--parts", type=Path, default=ROOT / "out/resource-packs")
    parser.add_argument("--credential-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        files = resource_files(args.parts)
        print(json.dumps({"local_verified": len(files), "total_bytes": sum(p["size"] for _, p in files)}), flush=True)
        result = upload(GitHub(credential(args.credential_dir)), args.repo, args.release_id, files)
        print(json.dumps(result, indent=2), flush=True)
        return 0
    except urllib.error.HTTPError as error:
        print("Resource publication failed: HTTP " + str(error.code), file=sys.stderr)
    except (ValueError, OSError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
