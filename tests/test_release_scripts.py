from __future__ import annotations

import base64
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_release_manifest_signs_canonical_metadata_and_verifies_with_public_key(artifact_dir: Path) -> None:
    release_manifest = _load_script("release_manifest")
    private_key = Ed25519PrivateKey.generate()
    private_key_b64 = base64.b64encode(
        private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    ).decode("ascii")
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode("ascii")
    artifact = artifact_dir / "SwimTunes-0.2.0-beta.1-windows-x64.zip"
    artifact.write_bytes(b"signed portable archive")
    manifest_path = artifact_dir / "update-manifest.json"
    signature_path = artifact_dir / "update-manifest.json.sig"

    release_manifest.write_signed_manifest(
        artifact=artifact,
        version="0.2.0-beta.1",
        publisher="CN=TJ Egbejimba",
        private_key_b64=private_key_b64,
        output=manifest_path,
        signature_output=signature_path,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == {
        "artifact": artifact.name,
        "publisher": "CN=TJ Egbejimba",
        "sha256": "b77113a7d816f1169a4f5c65a049c7049e5f82c68c7abcaaaedfc349eaeac52f",
        "size": 23,
        "version": "0.2.0-beta.1",
    }
    release_manifest.verify_manifest_signature(manifest_path, signature_path, public_key_b64)


def test_payload_audit_rejects_app_source_in_release_directory_and_archives(artifact_dir: Path) -> None:
    audit_release_payload = _load_script("audit_release_payload")
    payload = artifact_dir / "payload"
    payload.mkdir()
    (payload / "update-manifest.json").write_text("{}", encoding="utf-8")
    (payload / "SwimTunes-0.2.0-beta.1-windows-x64.zip").write_bytes(b"placeholder")

    audit_release_payload.audit_payload(payload)

    (payload / "src").mkdir()
    (payload / "src" / "swimtunes.py").write_text("print('source leak')", encoding="utf-8")
    with pytest.raises(audit_release_payload.PayloadAuditError, match="app source"):
        audit_release_payload.audit_payload(payload)

    (payload / "src" / "swimtunes.py").unlink()
    (payload / "src").rmdir()
    with zipfile.ZipFile(payload / "SwimTunes-0.2.0-beta.1-windows-x64.zip", "w") as archive:
        archive.writestr("tests/test_workflow.py", "def test_leak(): pass")
    with pytest.raises(audit_release_payload.PayloadAuditError, match="app source"):
        audit_release_payload.audit_payload(payload)
