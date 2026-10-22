#!/usr/bin/env python3
"""Seal only the pinned Biko3 NRO for CI; never reads game data or Switch keys."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

MAGIC = b"BIKO3ENC\x01"
HEADER = struct.Struct("<9sQ32s12s")
PINNED_SHA256 = "31320a8ce44f8e1826fc90ed5b26cb714c551413b286f8c69adb03f9e8b07178"
MAX_GUEST_BYTES = 64 * 1024 * 1024


def inspect_container(data):
    if len(data) < HEADER.size + 16:
        raise ValueError("Encrypted guest container is truncated")
    magic, size, digest, nonce = HEADER.unpack_from(data)
    if magic != MAGIC or size > MAX_GUEST_BYTES or size < 0x80 or len(data) != HEADER.size + size + 16:
        raise ValueError("Unsupported encrypted guest container")
    if digest.hex() != PINNED_SHA256:
        raise ValueError("Encrypted guest does not match the pinned NRO")
    return {"version": 1, "plaintext_bytes": size, "plaintext_sha256": digest.hex(),
            "container_bytes": len(data), "container_sha256": hashlib.sha256(data).hexdigest()}


def read_key(path=None, *, environment=False):
    if environment:
        value = os.environ.get("BIKO3_GUEST_KEY", "")
    elif path is None or str(path) == "-":
        value = sys.stdin.read()
    else:
        value = Path(path).read_text(encoding="ascii")
    try:
        key = bytes.fromhex(value.strip())
    except ValueError:
        raise ValueError("Guest key must contain exactly 64 hexadecimal characters") from None
    if len(key) != 32:
        raise ValueError("Guest key must contain exactly 64 hexadecimal characters")
    return key


def seal(data, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(key) != 32:
        raise ValueError("Guest key must be 32 bytes")
    if hashlib.sha256(data).hexdigest() != PINNED_SHA256 or data[0x10:0x14] != b"NRO0":
        raise ValueError("Only the pinned original Biko3 NRO may be sealed")
    nonce = os.urandom(12)
    header = HEADER.pack(MAGIC, len(data), bytes.fromhex(PINNED_SHA256), nonce)
    return header + AESGCM(key).encrypt(nonce, data, header)


def open_container(data, key):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    inspect_container(data)
    if len(key) != 32:
        raise ValueError("Guest key must be 32 bytes")
    try:
        plain = AESGCM(key).decrypt(HEADER.unpack_from(data)[3], data[HEADER.size:], data[:HEADER.size])
    except InvalidTag:
        raise ValueError("Encrypted guest authentication failed") from None
    if hashlib.sha256(plain).hexdigest() != PINNED_SHA256 or plain[0x10:0x14] != b"NRO0":
        raise ValueError("Decrypted NRO digest mismatch")
    return plain


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("seal")
    create.add_argument("source", type=Path)
    create.add_argument("destination", type=Path)
    create.add_argument("--key-file", type=Path, default=Path(".tools/private/biko3-guest.key"))
    create.add_argument("--manifest", type=Path)
    unlock = commands.add_parser("open")
    unlock.add_argument("source", type=Path)
    unlock.add_argument("destination", type=Path)
    unlock.add_argument("--key-file", type=Path)
    args = parser.parse_args(argv)
    if args.destination.exists():
        raise ValueError("Destination already exists")
    if args.command == "seal":
        # Check the source before generating or writing a private key.
        data = args.source.read_bytes()
        if hashlib.sha256(data).hexdigest() != PINNED_SHA256:
            raise ValueError("Only the pinned original Biko3 NRO may be sealed")
        if args.key_file.exists():
            key = read_key(args.key_file)
        else:
            key = os.urandom(32)
            args.key_file.parent.mkdir(parents=True, exist_ok=True)
            with args.key_file.open("x", encoding="ascii") as output:
                output.write(key.hex() + "\n")
            args.key_file.chmod(0o600)
        packed = seal(data, key)
        args.destination.parent.mkdir(parents=True, exist_ok=True)
        args.destination.write_bytes(packed)
        report = inspect_container(packed)
        if args.manifest:
            args.manifest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    else:
        key = read_key(args.key_file, environment=args.key_file is None)
        plain = open_container(args.source.read_bytes(), key)
        args.destination.parent.mkdir(parents=True, exist_ok=True)
        args.destination.write_bytes(plain)
        report = {"plaintext_bytes": len(plain), "plaintext_sha256": hashlib.sha256(plain).hexdigest()}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        sys.exit(1)
