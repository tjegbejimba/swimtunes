from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_signed_manifest(
    *,
    artifact: Path,
    version: str,
    publisher: str,
    private_key_b64: str,
    output: Path,
    signature_output: Path,
) -> dict[str, object]:
    artifact_bytes = artifact.read_bytes()
    manifest: dict[str, object] = {
        "version": version,
        "artifact": artifact.name,
        "size": len(artifact_bytes),
        "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "publisher": publisher,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_manifest_bytes(manifest))
    signature_output.parent.mkdir(parents=True, exist_ok=True)
    signature_output.write_bytes(
        _private_key_from_base64(private_key_b64).sign(canonical_manifest_bytes(manifest))
    )
    return manifest


def verify_manifest_signature(manifest_path: Path, signature_path: Path, public_key_b64: str) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    try:
        _public_key_from_base64(public_key_b64).verify(
            signature_path.read_bytes(),
            canonical_manifest_bytes(manifest),
        )
    except InvalidSignature as exc:
        raise ValueError("manifest signature did not verify with the pinned public key") from exc


def _private_key_from_base64(value: str) -> Ed25519PrivateKey:
    key_bytes = base64.b64decode(value, validate=True)
    if len(key_bytes) != 32:
        raise ValueError("Ed25519 private key must be 32 raw bytes encoded as base64")
    return Ed25519PrivateKey.from_private_bytes(key_bytes)


def _public_key_from_base64(value: str) -> Ed25519PublicKey:
    key_bytes = base64.b64decode(value, validate=True)
    if len(key_bytes) != 32:
        raise ValueError("Ed25519 public key must be 32 raw bytes encoded as base64")
    return Ed25519PublicKey.from_public_bytes(key_bytes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and verify signed SwimTunes update manifests.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate")
    generate.add_argument("--artifact", type=Path, required=True)
    generate.add_argument("--version", required=True)
    generate.add_argument("--publisher", required=True)
    generate.add_argument("--private-key-base64")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--signature-output", type=Path, required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--signature", type=Path, required=True)
    verify.add_argument("--public-key-base64", required=True)

    args = parser.parse_args()
    if args.command == "generate":
        private_key_b64 = args.private_key_base64 or os.environ.get("SWIMTUNES_UPDATE_PRIVATE_KEY")
        if not private_key_b64:
            raise SystemExit("missing --private-key-base64 or SWIMTUNES_UPDATE_PRIVATE_KEY")
        write_signed_manifest(
            artifact=args.artifact,
            version=args.version,
            publisher=args.publisher,
            private_key_b64=private_key_b64,
            output=args.output,
            signature_output=args.signature_output,
        )
    else:
        verify_manifest_signature(args.manifest, args.signature, args.public_key_base64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
