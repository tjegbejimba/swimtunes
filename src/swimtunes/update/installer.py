from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from swimtunes.launcher import COMPLETE_MARKER, DEFAULT_EXECUTABLE, POINTER_FILE, PREVIOUS_POINTER_FILE, VERIFICATION_RECORD
from swimtunes.update import (
    PublisherVerifier,
    ReleaseArtifact,
    UpdateCandidate,
    UpdateCheckResult,
    UpdateCheckStatus,
    WindowsAuthenticodePublisherVerifier,
)


@dataclass(frozen=True)
class PortableInstallOutcome:
    installed: bool
    version: str | None = None
    warning: str | None = None
    relaunch_required: bool = False


@dataclass(frozen=True)
class PortableLayout:
    root: Path

    @property
    def versions_dir(self) -> Path:
        return self.root / "versions"

    def version_dir(self, version: str) -> Path:
        return self.versions_dir / version

    def staging_dir(self, version: str) -> Path:
        return self.versions_dir / f"{version}.staging"

    @property
    def current_pointer(self) -> Path:
        return self.root / POINTER_FILE

    @property
    def previous_pointer(self) -> Path:
        return self.root / PREVIOUS_POINTER_FILE

    def ensure_writable(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        probe = self.root / ".swimtunes-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()

    def current_version(self) -> str | None:
        return _read_pointer(self.current_pointer)


class PortableUpdateInstaller:
    def __init__(self, layout: PortableLayout, publisher_verifier: PublisherVerifier | None = None) -> None:
        self.layout = layout
        self.publisher_verifier = publisher_verifier

    def install(self, candidate: UpdateCandidate, artifact: ReleaseArtifact) -> PortableInstallOutcome:
        try:
            self.layout.ensure_writable()
            artifact_bytes = artifact.bytes()
            staging = self.layout.staging_dir(candidate.version)
            final = self.layout.version_dir(candidate.version)
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            staged_archive = staging / ".candidate.zip"
            staged_archive.write_bytes(artifact_bytes)
            staged_bytes = staged_archive.read_bytes()
            digest = hashlib.sha256(staged_bytes).hexdigest()
            if digest.lower() != candidate.sha256.lower():
                shutil.rmtree(staging, ignore_errors=True)
                return _warn("Update artifact hash did not match signed metadata after staging; continuing current version.")
            if len(staged_bytes) != candidate.size:
                shutil.rmtree(staging, ignore_errors=True)
                return _warn("Update artifact size did not match signed metadata after staging; continuing current version.")
            _extract_zip_safely(staged_archive, staging)
            staged_archive.unlink()
            executable = staging / DEFAULT_EXECUTABLE
            if not executable.is_file():
                shutil.rmtree(staging, ignore_errors=True)
                return _warn("Staged update did not contain SwimTunes.exe; continuing current version.")
            publisher_warning = self._verify_staged_publishers(staging, candidate.publisher)
            if publisher_warning is not None:
                shutil.rmtree(staging, ignore_errors=True)
                return _warn(publisher_warning)

            record = {
                "version": candidate.version,
                "artifact": candidate.artifact_name,
                "sha256": candidate.sha256,
                "publisher": candidate.publisher,
                "executable": str(DEFAULT_EXECUTABLE).replace(os.sep, "/"),
            }
            _write_json_atomic(staging / VERIFICATION_RECORD, record)
            _write_text_atomic(staging / COMPLETE_MARKER, "ok\n")

            if final.exists():
                shutil.rmtree(final)
            staging.rename(final)

            previous_version = self.layout.current_version()
            if previous_version:
                _write_json_atomic(self.layout.previous_pointer, {"version": previous_version})
            _write_json_atomic(self.layout.current_pointer, {"version": candidate.version})
            return PortableInstallOutcome(
                installed=True,
                version=candidate.version,
                relaunch_required=True,
            )
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            return _warn(f"Update installation failed: {exc}; continuing current version.")

    def _verify_staged_publishers(self, staging: Path, expected_publisher: str) -> str | None:
        if self.publisher_verifier is None:
            return None
        pe_files = sorted(
            path
            for path in staging.rglob("*")
            if path.is_file() and path.suffix.lower() in {".exe", ".dll"}
        )
        if not pe_files:
            return "Staged update did not contain signed executable files; continuing current version."
        for path in pe_files:
            trusted, publisher = self.publisher_verifier.verify(
                ReleaseArtifact(name=path.name, path=path),
                expected_publisher,
            )
            if not trusted:
                return (
                    f"Staged update publisher '{publisher}' on {path.name} is not the trusted "
                    f"publisher '{expected_publisher}'; continuing current version."
                )
        return None


def install_update_at_startup(
    *,
    layout: PortableLayout,
    update_result: UpdateCheckResult,
    publisher_verifier: PublisherVerifier | None = None,
) -> PortableInstallOutcome:
    if update_result.status is UpdateCheckStatus.WARNING:
        return _warn(update_result.warning or "Update check failed; continuing current version.")
    if update_result.status is not UpdateCheckStatus.ELIGIBLE or update_result.candidate is None:
        return PortableInstallOutcome(installed=False)
    artifact = getattr(update_result, "artifact", None)
    if artifact is None:
        return _warn("Update was verified but no staged artifact was available; continuing current version.")
    verifier = publisher_verifier or WindowsAuthenticodePublisherVerifier()
    return PortableUpdateInstaller(layout, publisher_verifier=verifier).install(update_result.candidate, artifact)


def default_portable_layout(executable: Path | None = None) -> PortableLayout | None:
    env_root = os.environ.get("SWIMTUNES_PORTABLE_ROOT")
    if env_root:
        return PortableLayout(Path(env_root))
    if executable is None:
        executable_env = os.environ.get("SWIMTUNES_EXECUTABLE")
        if executable_env:
            executable = Path(executable_env)
        elif getattr(sys, "frozen", False):
            executable = Path(sys.executable)
        else:
            return None
    resolved = executable.resolve()
    parts = resolved.parts
    if len(parts) >= 5 and parts[-4] == "versions":
        return PortableLayout(Path(*parts[:-4]))
    return None


def _extract_zip_safely(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        dest_root = destination.resolve()
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if dest_root != target and dest_root not in target.parents:
                raise ValueError("update archive contains a path outside the staging directory")
        archive.extractall(destination)


def _read_pointer(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = payload.get("version") if isinstance(payload, dict) else None
    return version if isinstance(version, str) else None


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    _write_text_atomic(path, json.dumps(payload, sort_keys=True))


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.new")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)


def _warn(message: str) -> PortableInstallOutcome:
    return PortableInstallOutcome(installed=False, warning=message)
