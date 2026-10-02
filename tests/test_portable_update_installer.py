from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
import base64

from swimtunes.update import UpdateChecker, UpdateConfig, ReleaseFeed

from swimtunes.update import ReleaseArtifact, UpdateCandidate, UpdateCheckResult, UpdateCheckStatus, canonical_manifest_bytes
from swimtunes.update.installer import PortableUpdateInstaller, PortableLayout, install_update_at_startup
from swimtunes.launcher import choose_launch_target



class SignedFixtureFeed(ReleaseFeed):
    def __init__(self, manifest: dict[str, object], signature: bytes, artifact: ReleaseArtifact) -> None:
        self.manifest = manifest
        self.signature = signature
        self.artifact = artifact

    def latest(self) -> tuple[bytes, bytes | None]:
        return canonical_manifest_bytes(self.manifest), self.signature

    def download_artifact(self, artifact_name: str) -> ReleaseArtifact:
        assert artifact_name == self.manifest["artifact"]
        return self.artifact


class TrustedPublisherVerifier:
    def __init__(self) -> None:
        self.checked: list[Path] = []

    def verify(self, artifact: ReleaseArtifact, expected_publisher: str):
        assert artifact.path is not None
        self.checked.append(artifact.path)
        return True, expected_publisher


class UntrustedPublisherVerifier:
    def __init__(self, publisher: str = "Mallory") -> None:
        self.publisher = publisher
        self.checked: list[Path] = []

    def verify(self, artifact: ReleaseArtifact, expected_publisher: str):
        assert artifact.path is not None
        self.checked.append(artifact.path)
        return False, self.publisher


def _candidate(version: str, payload: bytes) -> UpdateCandidate:
    return UpdateCandidate(
        version=version,
        artifact_name=f"SwimTunes-{version}-windows-x64.zip",
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        publisher="TJ Egbejimba",
    )


def _zip_app(artifact_dir: Path, version: str, exe_text: str = "app") -> ReleaseArtifact:
    zip_path = artifact_dir / f"candidate-{version}.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("SwimTunes/SwimTunes.exe", exe_text)
        archive.writestr("SwimTunes/_internal/app.dll", "dll")
        archive.writestr("SwimTunes/_internal/tools/TOOL_MANIFEST.json", "{}")
    return ReleaseArtifact(name=zip_path.name, path=zip_path)


def _make_installed_version(root: Path, version: str, exe_text: str = "old") -> Path:
    app_dir = root / "versions" / version / "SwimTunes"
    app_dir.mkdir(parents=True)
    (app_dir / "SwimTunes.exe").write_text(exe_text, encoding="utf-8")
    record = {
        "version": version,
        "sha256": "installed",
        "publisher": "TJ Egbejimba",
        "executable": "SwimTunes/SwimTunes.exe",
    }
    (root / "versions" / version / "UPDATE_VERIFIED.json").write_text(json.dumps(record), encoding="utf-8")
    (root / "versions" / version / "INSTALL_COMPLETE").write_text("ok", encoding="utf-8")
    return app_dir / "SwimTunes.exe"


def _point_current(root: Path, version: str) -> None:
    (root / "current.json").write_text(json.dumps({"version": version}), encoding="utf-8")


def test_successful_update_is_staged_verified_promoted_and_selected_by_launcher(artifact_dir: Path) -> None:
    root = artifact_dir / "portable"
    old_exe = _make_installed_version(root, "0.1.0")
    _point_current(root, "0.1.0")
    artifact = _zip_app(artifact_dir, "0.2.0", "new")
    candidate = _candidate("0.2.0", artifact.bytes())

    verifier = TrustedPublisherVerifier()

    result = PortableUpdateInstaller(PortableLayout(root), publisher_verifier=verifier).install(candidate, artifact)

    assert result.installed
    assert {path.name for path in verifier.checked} == {"SwimTunes.exe", "app.dll"}
    assert result.warning is None
    assert (root / "previous.json").read_text(encoding="utf-8") == json.dumps({"version": "0.1.0"})
    target = choose_launch_target(root)
    assert target == root / "versions" / "0.2.0" / "SwimTunes" / "SwimTunes.exe"
    assert target.read_text(encoding="utf-8") == "new"
    assert old_exe.exists()


def test_offline_or_ineligible_update_preserves_current_version(artifact_dir: Path) -> None:
    root = artifact_dir / "portable"
    old_exe = _make_installed_version(root, "0.1.0")
    _point_current(root, "0.1.0")
    update_result = UpdateCheckResult(UpdateCheckStatus.WARNING, warning="Update check failed: offline")

    outcome = install_update_at_startup(layout=PortableLayout(root), update_result=update_result)

    assert not outcome.installed
    assert "offline" in outcome.warning
    assert choose_launch_target(root) == old_exe


def test_unwritable_portable_folder_reports_warning_and_keeps_old_version(artifact_dir: Path) -> None:
    root = artifact_dir / "portable"
    old_exe = _make_installed_version(root, "0.1.0")
    _point_current(root, "0.1.0")
    artifact = _zip_app(artifact_dir, "0.2.0", "new")
    candidate = _candidate("0.2.0", artifact.bytes())

    class ReadOnlyLayout(PortableLayout):
        def ensure_writable(self) -> None:
            raise PermissionError("portable folder is read-only")

    result = PortableUpdateInstaller(ReadOnlyLayout(root)).install(candidate, artifact)

    assert not result.installed
    assert "read-only" in result.warning
    assert choose_launch_target(root) == old_exe


def test_interrupted_staging_is_ignored_and_replaced_on_next_success(artifact_dir: Path) -> None:
    root = artifact_dir / "portable"
    _make_installed_version(root, "0.1.0")
    _point_current(root, "0.1.0")
    staging = root / "versions" / "0.2.0.staging"
    (staging / "SwimTunes").mkdir(parents=True)
    (staging / "SwimTunes" / "SwimTunes.exe").write_text("partial", encoding="utf-8")
    artifact = _zip_app(artifact_dir, "0.2.0", "complete")
    candidate = _candidate("0.2.0", artifact.bytes())

    result = PortableUpdateInstaller(PortableLayout(root)).install(candidate, artifact)

    assert result.installed
    assert not staging.exists()
    assert choose_launch_target(root).read_text(encoding="utf-8") == "complete"


def test_crash_between_promotion_and_pointer_flip_keeps_launching_previous_version(artifact_dir: Path) -> None:
    root = artifact_dir / "portable"
    old_exe = _make_installed_version(root, "0.1.0")
    _point_current(root, "0.1.0")
    _make_installed_version(root, "0.2.0", "new")

    assert choose_launch_target(root) == old_exe


def test_failed_verification_after_staging_does_not_promote_or_launch_candidate(artifact_dir: Path) -> None:
    root = artifact_dir / "portable"
    old_exe = _make_installed_version(root, "0.1.0")
    _point_current(root, "0.1.0")
    artifact = _zip_app(artifact_dir, "0.2.0", "new")
    candidate = _candidate("0.2.0", b"different bytes")

    result = PortableUpdateInstaller(PortableLayout(root)).install(candidate, artifact)

    assert not result.installed
    assert "hash" in result.warning
    assert not (root / "versions" / "0.2.0").exists()
    assert choose_launch_target(root) == old_exe


@pytest.mark.parametrize("missing", ["UPDATE_VERIFIED.json", "INSTALL_COMPLETE", "SwimTunes/SwimTunes.exe"])
def test_launcher_never_starts_partial_update_and_falls_back(artifact_dir: Path, missing: str) -> None:
    root = artifact_dir / "portable"
    old_exe = _make_installed_version(root, "0.1.0")
    _make_installed_version(root, "0.2.0", "partial")
    _point_current(root, "0.2.0")
    (root / "previous.json").write_text(json.dumps({"version": "0.1.0"}), encoding="utf-8")
    target = root / "versions" / "0.2.0" / missing
    if target.exists():
        if target.is_dir():
            os.rmdir(target)
        else:
            target.unlink()

    assert choose_launch_target(root) == old_exe


def test_startup_installs_candidate_from_signed_feed_fixture(artifact_dir: Path) -> None:
    root = artifact_dir / "portable"
    _make_installed_version(root, "0.1.0")
    _point_current(root, "0.1.0")
    artifact = _zip_app(artifact_dir, "0.2.0", "new")
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(
        private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode("ascii")
    manifest = {
        "version": "0.2.0",
        "artifact": artifact.name,
        "size": artifact.byte_size,
        "sha256": hashlib.sha256(artifact.bytes()).hexdigest(),
        "publisher": "TJ Egbejimba",
    }
    signature = private_key.sign(canonical_manifest_bytes(manifest))
    update_result = UpdateChecker(
        config=UpdateConfig(
            current_version="0.1.0",
            public_key=public_key,
            expected_publisher="TJ Egbejimba",
        ),
        feed=SignedFixtureFeed(manifest, signature, artifact),
        publisher_verifier=TrustedPublisherVerifier(),
    ).check()

    outcome = install_update_at_startup(
        layout=PortableLayout(root),
        update_result=update_result,
        publisher_verifier=TrustedPublisherVerifier(),
    )

    assert outcome.installed
    assert choose_launch_target(root).read_text(encoding="utf-8") == "new"


def test_staged_executable_wrong_publisher_is_rejected_before_promotion(artifact_dir: Path) -> None:
    root = artifact_dir / "portable"
    old_exe = _make_installed_version(root, "0.1.0")
    _point_current(root, "0.1.0")
    artifact = _zip_app(artifact_dir, "0.2.0", "new")
    candidate = _candidate("0.2.0", artifact.bytes())

    result = PortableUpdateInstaller(
        PortableLayout(root),
        publisher_verifier=UntrustedPublisherVerifier(),
    ).install(candidate, artifact)

    assert not result.installed
    assert "publisher 'Mallory'" in result.warning
    assert not (root / "versions" / "0.2.0").exists()
    assert choose_launch_target(root) == old_exe
