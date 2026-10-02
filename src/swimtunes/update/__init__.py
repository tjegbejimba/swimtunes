from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from swimtunes import __version__
from swimtunes import _release_config
from swimtunes.paths import default_workspace


PUBLIC_RELEASE_REPOSITORY = "tjegbejimba/swimtunes-releases"
DEFAULT_MANIFEST_ASSET = "update-manifest.json"
DEFAULT_SIGNATURE_ASSET = "update-manifest.json.sig"


class UpdateCheckStatus(Enum):
    NO_UPDATE = "no_update"
    ELIGIBLE = "eligible"
    WARNING = "warning"


class FeedUnavailable(RuntimeError):
    pass


class NoUpdateAvailable(RuntimeError):
    pass


class SignatureVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseArtifact:
    name: str
    content: bytes | None = None
    path: Path | None = None

    def bytes(self) -> bytes:
        if self.content is not None:
            return self.content
        if self.path is None:
            raise ValueError("release artifact has neither content nor path")
        return self.path.read_bytes()

    @property
    def byte_size(self) -> int:
        if self.content is not None:
            return len(self.content)
        if self.path is None:
            raise ValueError("release artifact has neither content nor path")
        return self.path.stat().st_size


@dataclass(frozen=True)
class UpdateCandidate:
    version: str
    artifact_name: str
    size: int
    sha256: str
    publisher: str


@dataclass(frozen=True)
class UpdateCheckResult:
    status: UpdateCheckStatus
    candidate: UpdateCandidate | None = None
    warning: str | None = None
    artifact: ReleaseArtifact | None = None


@dataclass(frozen=True)
class UpdateConfig:
    current_version: str
    public_key: str
    expected_publisher: str
    release_repository: str = PUBLIC_RELEASE_REPOSITORY


class ReleaseFeed(Protocol):
    def latest(self) -> tuple[bytes, bytes | None]:
        """Fetch signed update metadata from the public release feed."""

    def download_artifact(self, artifact_name: str) -> ReleaseArtifact:
        """Fetch the candidate artifact bytes or file without executing it."""


class PublisherVerifier(Protocol):
    def verify(self, artifact: ReleaseArtifact, expected_publisher: str) -> tuple[bool, str]:
        """Return whether the artifact's platform signature matches the publisher."""


@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[int | str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        match = re.fullmatch(
            r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
            r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
            value,
        )
        if match is None:
            raise ValueError(f"invalid semantic version: {value}")
        prerelease: list[int | str] = []
        if match.group(4):
            for part in match.group(4).split("."):
                prerelease.append(int(part) if part.isdigit() else part)
        return cls(
            major=int(match.group(1)),
            minor=int(match.group(2)),
            patch=int(match.group(3)),
            prerelease=tuple(prerelease),
        )

    def __lt__(self, other: "SemVer") -> bool:
        if (self.major, self.minor, self.patch) != (other.major, other.minor, other.patch):
            return (self.major, self.minor, self.patch) < (
                other.major,
                other.minor,
                other.patch,
            )
        if not self.prerelease and other.prerelease:
            return False
        if self.prerelease and not other.prerelease:
            return True
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            if isinstance(left, int) and isinstance(right, str):
                return True
            if isinstance(left, str) and isinstance(right, int):
                return False
            return left < right
        return len(self.prerelease) < len(other.prerelease)

    def __le__(self, other: "SemVer") -> bool:
        return self == other or self < other


def canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


class UpdateChecker:
    def __init__(
        self,
        *,
        config: UpdateConfig,
        feed: ReleaseFeed,
        publisher_verifier: PublisherVerifier,
    ) -> None:
        self.config = config
        self.feed = feed
        self.publisher_verifier = publisher_verifier

    def check(self) -> UpdateCheckResult:
        try:
            manifest_bytes, signature = self.feed.latest()
            if signature is None:
                return _warning("Update metadata is unsigned; continuing current version.")
            manifest = self._load_manifest(manifest_bytes)
            candidate = self._candidate_from_manifest(manifest)
            self._verify_signature(manifest, signature)
            if SemVer.parse(candidate.version) <= SemVer.parse(self.config.current_version):
                return _warning(
                    f"Update {candidate.version} is not newer than installed version "
                    f"{self.config.current_version}; continuing current version."
                )
            if candidate.publisher != self.config.expected_publisher:
                return _warning(
                    f"Update metadata publisher '{candidate.publisher}' is not the trusted publisher "
                    f"'{self.config.expected_publisher}'; continuing current version."
                )
            artifact = self.feed.download_artifact(candidate.artifact_name)
            if artifact.byte_size != candidate.size:
                return _warning("Update artifact size did not match signed metadata; continuing current version.")
            digest = hashlib.sha256(artifact.bytes()).hexdigest()
            if digest.lower() != candidate.sha256.lower():
                return _warning("Update artifact hash did not match signed metadata; continuing current version.")
            return UpdateCheckResult(UpdateCheckStatus.ELIGIBLE, candidate=candidate, artifact=artifact)
        except NoUpdateAvailable:
            return UpdateCheckResult(UpdateCheckStatus.NO_UPDATE)
        except FeedUnavailable as exc:
            return _warning(f"Update check failed: {exc}; continuing current version.")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            return _warning(f"Update metadata is malformed: {exc}; continuing current version.")
        except SignatureVerificationError:
            return _warning("Update metadata signature is not trusted; continuing current version.")
        except OSError as exc:
            return _warning(f"Update check failed: {exc}; continuing current version.")

    def _load_manifest(self, manifest_bytes: bytes) -> dict[str, object]:
        parsed = json.loads(manifest_bytes.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("top-level metadata must be an object")
        return parsed

    def _verify_signature(self, manifest: dict[str, object], signature: bytes) -> None:
        try:
            public_key_bytes = base64.b64decode(self.config.public_key, validate=True)
            public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            public_key.verify(signature, canonical_manifest_bytes(manifest))
        except (InvalidSignature, ValueError) as exc:
            raise SignatureVerificationError from exc

    def _candidate_from_manifest(self, manifest: dict[str, object]) -> UpdateCandidate:
        candidate = UpdateCandidate(
            version=_required_str(manifest, "version"),
            artifact_name=_required_str(manifest, "artifact"),
            size=_required_int(manifest, "size"),
            sha256=_required_str(manifest, "sha256"),
            publisher=_required_str(manifest, "publisher"),
        )
        if not re.fullmatch(r"[0-9a-fA-F]{64}", candidate.sha256):
            raise ValueError("sha256 must be 64 hexadecimal characters")
        SemVer.parse(candidate.version)
        return candidate


class GitHubReleaseFeed:
    def __init__(
        self,
        *,
        repository: str = PUBLIC_RELEASE_REPOSITORY,
        manifest_asset: str = DEFAULT_MANIFEST_ASSET,
        signature_asset: str = DEFAULT_SIGNATURE_ASSET,
        download_dir: Path | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        self.repository = repository
        self.manifest_asset = manifest_asset
        self.signature_asset = signature_asset
        self.download_dir = download_dir or default_workspace() / "updates"
        self.timeout_seconds = timeout_seconds
        self._assets: dict[str, str] = {}

    def latest(self) -> tuple[bytes, bytes | None]:
        release = self._request_json(f"https://api.github.com/repos/{self.repository}/releases/latest")
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise FeedUnavailable("public release feed did not include assets")
        self._assets = {
            asset["name"]: asset["browser_download_url"]
            for asset in assets
            if isinstance(asset, dict)
            and isinstance(asset.get("name"), str)
            and isinstance(asset.get("browser_download_url"), str)
        }
        return self._download_asset_bytes(self.manifest_asset), self._download_asset_bytes(
            self.signature_asset,
            required=False,
        )

    def download_artifact(self, artifact_name: str) -> ReleaseArtifact:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        destination = self.download_dir / Path(artifact_name).name
        destination.write_bytes(self._download_asset_bytes(artifact_name))
        return ReleaseArtifact(name=artifact_name, path=destination)

    def _request_json(self, url: str) -> dict[str, object]:
        try:
            with urllib.request.urlopen(self._request(url), timeout=self.timeout_seconds) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NoUpdateAvailable from exc
            raise FeedUnavailable(str(exc)) from exc
        except OSError as exc:
            raise FeedUnavailable(str(exc)) from exc
        parsed = json.loads(data.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise FeedUnavailable("public release feed response was malformed")
        return parsed

    def _download_asset_bytes(self, name: str, *, required: bool = True) -> bytes | None:
        url = self._assets.get(name)
        if url is None:
            if required:
                raise FeedUnavailable(f"public release feed is missing {name}")
            return None
        try:
            with urllib.request.urlopen(self._request(url), timeout=self.timeout_seconds) as response:
                return response.read()
        except OSError as exc:
            raise FeedUnavailable(str(exc)) from exc

    def _request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "SwimTunes-updater",
            },
        )


class WindowsAuthenticodePublisherVerifier:
    def verify(self, artifact: ReleaseArtifact, expected_publisher: str) -> tuple[bool, str]:
        if platform.system() != "Windows":
            return False, "Authenticode verification is only available on Windows"
        if artifact.path is None:
            return False, "artifact was not downloaded to a file for Authenticode verification"
        command = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "$sig = Get-AuthenticodeSignature -LiteralPath $args[0]; "
                "[pscustomobject]@{Status=[string]$sig.Status; "
                "Subject=[string]$sig.SignerCertificate.Subject} | ConvertTo-Json -Compress"
            ),
            str(artifact.path),
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            return False, completed.stderr.strip() or "Authenticode verification failed"
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return False, "Authenticode verification returned malformed output"
        subject = str(payload.get("Subject") or "")
        status = str(payload.get("Status") or "")
        return status == "Valid" and expected_publisher in subject, subject or status


def default_update_checker() -> UpdateChecker | None:
    public_key = os.environ.get("SWIMTUNES_UPDATE_PUBLIC_KEY") or _release_config.UPDATE_PUBLIC_KEY
    publisher = os.environ.get("SWIMTUNES_UPDATE_PUBLISHER") or _release_config.UPDATE_PUBLISHER
    if not public_key or not publisher:
        return None
    return UpdateChecker(
        config=UpdateConfig(
            current_version=os.environ.get("SWIMTUNES_UPDATE_CURRENT_VERSION", __version__),
            public_key=public_key,
            expected_publisher=publisher,
        ),
        feed=GitHubReleaseFeed(
            repository=os.environ.get("SWIMTUNES_UPDATE_REPOSITORY", _release_config.UPDATE_REPOSITORY),
        ),
        publisher_verifier=WindowsAuthenticodePublisherVerifier(),
    )


def _required_str(manifest: dict[str, object], key: str) -> str:
    value = manifest[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _required_int(manifest: dict[str, object], key: str) -> int:
    value = manifest[key]
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _warning(message: str) -> UpdateCheckResult:
    return UpdateCheckResult(UpdateCheckStatus.WARNING, warning=message)
