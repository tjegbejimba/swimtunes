from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from swimtunes.update import (
    FeedUnavailable,
    NoUpdateAvailable,
    ReleaseArtifact,
    ReleaseFeed,
    UpdateChecker,
    UpdateCheckStatus,
    UpdateConfig,
)


def _canonical_bytes(manifest: dict[str, object]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


class FixtureFeed(ReleaseFeed):
    def __init__(
        self,
        *,
        manifest: dict[str, object] | bytes,
        signature: bytes | None,
        artifact_bytes: bytes = b"portable zip",
        artifact_name: str = "SwimTunes-0.2.0-windows-x64.zip",
        unavailable: bool = False,
        no_update: bool = False,
    ) -> None:
        self.manifest = manifest
        self.signature = signature
        self.artifact = ReleaseArtifact(name=artifact_name, content=artifact_bytes)
        self.unavailable = unavailable
        self.no_update = no_update
        self.downloaded = False

    def latest(self) -> tuple[bytes, bytes | None]:
        if self.unavailable:
            raise FeedUnavailable("network is unreachable")
        if self.no_update:
            raise NoUpdateAvailable()
        manifest_bytes = (
            self.manifest
            if isinstance(self.manifest, bytes)
            else _canonical_bytes(self.manifest)
        )
        return manifest_bytes, self.signature

    def download_artifact(self, artifact_name: str) -> ReleaseArtifact:
        self.downloaded = True
        assert artifact_name == self.artifact.name
        return self.artifact


class FixturePublisherVerifier:
    def __init__(self, *, trusted: bool = True, publisher: str = "TJ Egbejimba") -> None:
        self.trusted = trusted
        self.publisher = publisher
        self.checked: list[Path] = []

    def verify(self, artifact: ReleaseArtifact, expected_publisher: str):
        self.checked.append(artifact.path)
        return self.trusted and self.publisher == expected_publisher, self.publisher


@pytest.fixture
def signing_key():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private_key, base64.b64encode(public_key).decode("ascii")


@pytest.fixture
def signed_manifest(signing_key):
    private_key, public_key = signing_key
    artifact_bytes = b"portable zip"
    manifest = {
        "version": "0.2.0-beta.1",
        "artifact": "SwimTunes-0.2.0-windows-x64.zip",
        "size": len(artifact_bytes),
        "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "publisher": "TJ Egbejimba",
    }
    signature = private_key.sign(_canonical_bytes(manifest))
    return manifest, signature, public_key, artifact_bytes


def _checker(
    manifest: dict[str, object] | bytes,
    signature: bytes | None,
    public_key: str,
    artifact_bytes: bytes = b"portable zip",
    publisher_verifier: FixturePublisherVerifier | None = None,
) -> UpdateChecker:
    return UpdateChecker(
        config=UpdateConfig(
            current_version="0.1.0",
            public_key=public_key,
            expected_publisher="TJ Egbejimba",
        ),
        feed=FixtureFeed(
            manifest=manifest,
            signature=signature,
            artifact_bytes=artifact_bytes,
        ),
        publisher_verifier=publisher_verifier or FixturePublisherVerifier(),
    )


def test_no_update_when_public_feed_has_no_release(signing_key) -> None:
    _, public_key = signing_key
    feed = FixtureFeed(manifest={}, signature=None, no_update=True)
    checker = UpdateChecker(
        config=UpdateConfig(
            current_version="0.1.0",
            public_key=public_key,
            expected_publisher="TJ Egbejimba",
        ),
        feed=feed,
        publisher_verifier=FixturePublisherVerifier(),
    )

    result = checker.check()

    assert result.status is UpdateCheckStatus.NO_UPDATE
    assert result.candidate is None
    assert result.warning is None


@pytest.mark.parametrize("version", ["0.1.0", "0.0.9", "0.2.0-alpha.1"])
def test_same_older_or_older_prerelease_versions_are_rejected(signing_key, version) -> None:
    private_key, public_key = signing_key
    artifact_bytes = b"portable zip"
    manifest = {
        "version": version,
        "artifact": "SwimTunes-older-windows-x64.zip",
        "size": len(artifact_bytes),
        "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "publisher": "TJ Egbejimba",
    }
    signature = private_key.sign(_canonical_bytes(manifest))

    checker = UpdateChecker(
        config=UpdateConfig(
            current_version="0.2.0-alpha.1",
            public_key=public_key,
            expected_publisher="TJ Egbejimba",
        ),
        feed=FixtureFeed(manifest=manifest, signature=signature, artifact_bytes=artifact_bytes),
        publisher_verifier=FixturePublisherVerifier(),
    )

    result = checker.check()

    assert result.status is UpdateCheckStatus.WARNING
    assert result.candidate is None
    assert "is not newer than installed version" in result.warning


def test_valid_newer_signed_update_is_eligible(signed_manifest) -> None:
    manifest, signature, public_key, artifact_bytes = signed_manifest

    result = _checker(manifest, signature, public_key, artifact_bytes).check()

    assert result.status is UpdateCheckStatus.ELIGIBLE
    assert result.candidate is not None
    assert result.candidate.version == "0.2.0-beta.1"
    assert result.candidate.artifact_name == "SwimTunes-0.2.0-windows-x64.zip"
    assert result.candidate.sha256 == hashlib.sha256(artifact_bytes).hexdigest()
    assert result.warning is None


def test_beta_prerelease_is_newer_than_alpha_prerelease(signing_key) -> None:
    private_key, public_key = signing_key
    artifact_bytes = b"portable zip"
    manifest = {
        "version": "0.2.0-beta.1",
        "artifact": "SwimTunes-0.2.0-windows-x64.zip",
        "size": len(artifact_bytes),
        "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "publisher": "TJ Egbejimba",
    }
    signature = private_key.sign(_canonical_bytes(manifest))
    checker = UpdateChecker(
        config=UpdateConfig(
            current_version="0.2.0-alpha.9",
            public_key=public_key,
            expected_publisher="TJ Egbejimba",
        ),
        feed=FixtureFeed(manifest=manifest, signature=signature, artifact_bytes=artifact_bytes),
        publisher_verifier=FixturePublisherVerifier(),
    )

    result = checker.check()

    assert result.status is UpdateCheckStatus.ELIGIBLE
    assert result.candidate is not None
    assert result.candidate.version == "0.2.0-beta.1"


def test_offline_feed_does_not_block_startup(signing_key) -> None:
    _, public_key = signing_key
    checker = UpdateChecker(
        config=UpdateConfig(
            current_version="0.1.0",
            public_key=public_key,
            expected_publisher="TJ Egbejimba",
        ),
        feed=FixtureFeed(manifest={}, signature=None, unavailable=True),
        publisher_verifier=FixturePublisherVerifier(),
    )

    result = checker.check()

    assert result.status is UpdateCheckStatus.WARNING
    assert result.candidate is None
    assert "Update check failed" in result.warning
    assert "network is unreachable" in result.warning


@pytest.mark.parametrize(
    ("manifest", "signature", "expected"),
    [
        (b"{not-json", b"signature", "metadata is malformed"),
        ({}, b"signature", "metadata is malformed"),
        ({"version": "0.2.0"}, None, "metadata is unsigned"),
    ],
)
def test_malformed_or_unsigned_metadata_is_rejected(
    signing_key,
    manifest,
    signature,
    expected,
) -> None:
    _, public_key = signing_key

    result = _checker(manifest, signature, public_key).check()

    assert result.status is UpdateCheckStatus.WARNING
    assert result.candidate is None
    assert expected in result.warning


def test_bad_metadata_signature_is_rejected(signed_manifest) -> None:
    manifest, _signature, public_key, artifact_bytes = signed_manifest

    result = _checker(manifest, b"bad signature", public_key, artifact_bytes).check()

    assert result.status is UpdateCheckStatus.WARNING
    assert result.candidate is None
    assert "metadata signature is not trusted" in result.warning


def test_tampered_artifact_hash_is_rejected(signed_manifest) -> None:
    manifest, signature, public_key, _artifact_bytes = signed_manifest

    result = _checker(manifest, signature, public_key, b"tampered zip").check()

    assert result.status is UpdateCheckStatus.WARNING
    assert result.candidate is None
    assert "artifact hash did not match signed metadata" in result.warning


def test_wrong_designated_publisher_is_rejected(signed_manifest) -> None:
    manifest, signature, public_key, artifact_bytes = signed_manifest
    verifier = FixturePublisherVerifier(trusted=True, publisher="Mallory")

    result = _checker(manifest, signature, public_key, artifact_bytes, verifier).check()

    assert result.status is UpdateCheckStatus.WARNING
    assert result.candidate is None
    assert "artifact publisher 'Mallory' is not the trusted publisher" in result.warning
