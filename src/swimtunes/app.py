from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from swimtunes.adapters.ffmpeg import FfmpegMediaAdapter
from swimtunes.adapters.filesystem import LocalFilesystemAdapter
from swimtunes.adapters.ytdlp import YtDlpExtractor
from swimtunes.models import OperationResult, TransferOutcome, TransferStatus
from swimtunes.ports import Encoder, Extractor, FileSystem


class Workflow:
    def __init__(
        self,
        extractor: Extractor | None = None,
        encoder: Encoder | None = None,
        filesystem: FileSystem | None = None,
        workspace: Path | None = None,
    ) -> None:
        self.extractor = extractor or YtDlpExtractor()
        self.encoder = encoder or FfmpegMediaAdapter()
        self.filesystem = filesystem or LocalFilesystemAdapter()
        self.workspace = workspace or (Path.cwd() / ".swimtunes-work")

    def transfer_one(self, url: str, headphones_folder: Path) -> OperationResult:
        url = url.strip()
        if not self._is_supported_public_url(url):
            return OperationResult([
                TransferOutcome(
                    status=TransferStatus.ERROR,
                    source_url=url,
                    message="Enter a public YouTube or SoundCloud URL.",
                )
            ])

        try:
            self.filesystem.ensure_headphones_folder(headphones_folder)
        except Exception as exc:
            return OperationResult([
                TransferOutcome(
                    status=TransferStatus.ERROR,
                    source_url=url,
                    message=str(exc),
                )
            ])

        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
            source_item, extracted_path = self.extractor.extract(url, self.workspace / "extracted")
            filename = self.filesystem.safe_audio_filename(source_item.title)
            audio_path = self.workspace / "audio" / filename
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            media_info = self.encoder.encode_mp3(extracted_path, audio_path)
            if not media_info.is_stereo_192kbps_mp3:
                return OperationResult([
                    TransferOutcome(
                        status=TransferStatus.ERROR,
                        title=source_item.title,
                        source_url=url,
                        message="Audio File is not a stereo 192 kbps MP3.",
                    )
                ])
            probed_info = self.encoder.probe(audio_path)
            if not probed_info.is_stereo_192kbps_mp3:
                return OperationResult([
                    TransferOutcome(
                        status=TransferStatus.ERROR,
                        title=source_item.title,
                        source_url=url,
                        message="Encoded Audio File failed artifact verification.",
                    )
                ])
            transfer_path = self.filesystem.transfer_verified(audio_path, headphones_folder, filename)
            return OperationResult([
                TransferOutcome(
                    status=TransferStatus.TRANSFERRED,
                    title=source_item.title,
                    source_url=url,
                    transfer_path=transfer_path,
                    message="Transferred",
                )
            ])
        except Exception as exc:
            title = locals().get("source_item").title if "source_item" in locals() else None
            return OperationResult([
                TransferOutcome(
                    status=TransferStatus.ERROR,
                    title=title,
                    source_url=url,
                    message=str(exc),
                )
            ])

    def _is_supported_public_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        host = parsed.netloc.lower()
        return any(
            domain in host
            for domain in ("youtube.com", "youtu.be", "music.youtube.com", "soundcloud.com")
        )
