from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


@dataclass(frozen=True)
class SourceItem:
    source_id: str
    url: str
    title: str


@dataclass(frozen=True)
class SourcePlaylistEntry:
    source_id: str
    url: str
    title: str
    available: bool = True
    error: str | None = None


@dataclass(frozen=True)
class SourcePlaylist:
    url: str
    title: str | None = None
    entries: list[SourcePlaylistEntry] | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.entries is None:
            object.__setattr__(self, "entries", [])


@dataclass(frozen=True)
class MediaInfo:
    codec: str
    channels: int
    bit_rate: int
    is_cbr: bool

    @property
    def is_stereo_192kbps_mp3(self) -> bool:
        return (
            self.codec.lower() == "mp3"
            and self.channels == 2
            and 188_000 <= self.bit_rate <= 196_000
            and self.is_cbr
        )


@dataclass(frozen=True)
class AudioFile:
    source_item: SourceItem
    path: Path
    media_info: MediaInfo


class TransferStatus(Enum):
    DOWNLOADED = "downloaded"
    TRANSFERRED = "transferred"
    ERROR = "error"


@dataclass(frozen=True)
class TransferOutcome:
    status: TransferStatus
    title: str | None = None
    source_url: str | None = None
    audio_path: Path | None = None
    transfer_path: Path | None = None
    message: str = ""


@dataclass(frozen=True)
class OperationResult:
    outcomes: list[TransferOutcome]

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(
            outcome.status is TransferStatus.TRANSFERRED for outcome in self.outcomes
        )
