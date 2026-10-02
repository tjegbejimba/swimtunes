from __future__ import annotations

from pathlib import Path
from typing import Protocol

from swimtunes.models import (
    HeadphonesDeviceIdentity,
    HeadphonesFolderSelection,
    LocalLibrarySettings,
    MediaInfo,
    SourceItem,
    SourcePlaylist,
    TransferRecord,
)


class OperationCancelled(Exception):
    """Raised by adapters when a user cancellation interrupts work."""


class Extractor(Protocol):
    def preview(self, url: str) -> SourcePlaylist:
        """Resolve Source Playlist metadata without downloading Source Items."""

    def resolve(self, url: str) -> SourceItem:
        """Resolve stable Source Item metadata without downloading media."""

    def extract(self, url: str, work_dir: Path) -> tuple[SourceItem, Path]:
        """Extract a public Source Item and return metadata plus the local media path."""


class Encoder(Protocol):
    def encode_mp3(self, source_media: Path, output_path: Path) -> MediaInfo:
        """Create a stereo 192 kbps CBR MP3 Audio File and return probed media info."""

    def probe(self, path: Path) -> MediaInfo:
        """Inspect an Audio File artifact."""


class CancelToken(Protocol):
    @property
    def is_cancelled(self) -> bool:
        """Return True once the user has requested cancellation."""


class FileSystem(Protocol):
    def ensure_headphones_folder(self, folder: Path) -> None:
        """Reject absent or invalid Headphones Folders before extraction starts."""

    def safe_audio_filename(self, title: str) -> str:
        """Return a Headphones Device-safe MP3 filename for a Source Item title."""

    def transfer_verified(
        self,
        source: Path,
        headphones_folder: Path,
        filename: str,
        cancel_token: CancelToken | None = None,
    ) -> Path:
        """Copy, fsync, verify, and atomically complete a Transfer."""

    def free_bytes(self, folder: Path) -> int | None:
        """Return known writable free space for a Headphones Folder, or None if unknown."""


class TransferStateStore(Protocol):
    def get(self, source_id: str) -> TransferRecord | None:
        """Return the latest completed Transfer record for a stable Source Item."""

    def save(self, record: TransferRecord) -> None:
        """Persist a completed Transfer record."""

    def all(self) -> list[TransferRecord]:
        """Return known completed Transfer records."""


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

    def load_local_library(self) -> LocalLibrarySettings:
        """Load keep-local and the selected Local Library."""

    def save_local_library(self, settings: LocalLibrarySettings) -> None:
        """Persist keep-local and the selected Local Library."""
