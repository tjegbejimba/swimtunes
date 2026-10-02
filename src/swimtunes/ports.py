from __future__ import annotations

from pathlib import Path
from typing import Protocol

from swimtunes.models import MediaInfo, SourceItem, SourcePlaylist


class Extractor(Protocol):
    def preview(self, url: str) -> SourcePlaylist:
        """Resolve Source Playlist metadata without downloading Source Items."""

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
