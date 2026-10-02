from __future__ import annotations

from pathlib import Path
from typing import Protocol

from swimtunes.models import HeadphonesDeviceIdentity, HeadphonesFolderSelection, MediaInfo, SourceItem


class Extractor(Protocol):
    def extract(self, url: str, work_dir: Path) -> tuple[SourceItem, Path]:
        """Extract a public Source Item and return metadata plus the local media path."""


class Encoder(Protocol):
    def encode_mp3(self, source_media: Path, output_path: Path) -> MediaInfo:
        """Create a stereo 192 kbps CBR MP3 Audio File and return probed media info."""

    def probe(self, path: Path) -> MediaInfo:
        """Inspect an Audio File artifact."""


class FileSystem(Protocol):
    def ensure_headphones_folder(self, folder: Path) -> None:
        """Reject absent or invalid Headphones Folders before extraction starts."""

    def safe_audio_filename(self, title: str) -> str:
        """Return a Headphones Device-safe MP3 filename for a Source Item title."""

    def transfer_verified(self, source: Path, headphones_folder: Path, filename: str) -> Path:
        """Copy, fsync, verify, and atomically complete a Transfer."""


class DeviceIdentity(Protocol):
    def identity_for(self, folder: Path) -> HeadphonesDeviceIdentity | None:
        """Return the stable Headphones Device identity for a folder, or None if unverifiable."""

    def find_folder(
        self,
        expected_identity: HeadphonesDeviceIdentity,
        remembered_folder: Path,
    ) -> Path | None:
        """Find the remembered Headphones Folder after a mount point or drive-letter change."""


class SettingsStore(Protocol):
    def load_headphones_folder(self) -> HeadphonesFolderSelection | None:
        """Load the remembered Headphones Folder selection."""

    def save_headphones_folder(self, selection: HeadphonesFolderSelection) -> None:
        """Persist the remembered Headphones Folder selection."""
