from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from swimtunes.adapters.ffmpeg import FfmpegMediaAdapter
from swimtunes.adapters.filesystem import LocalFilesystemAdapter
from swimtunes.adapters.ytdlp import YtDlpExtractor
from swimtunes.models import OperationResult, SourcePlaylist, SourcePlaylistEntry, TransferOutcome, TransferStatus
from swimtunes.paths import default_workspace
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
        self.workspace = workspace or default_workspace()

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

        outcome = self._transfer_url(url, headphones_folder)
        return OperationResult([outcome])

    def transfer_many(self, urls: list[str], headphones_folder: Path) -> OperationResult:
        outcomes: list[TransferOutcome] = []
        for url in urls:
            outcomes.extend(self.transfer_one(url, headphones_folder).outcomes)
        return OperationResult(outcomes)

    def preview(self, url: str) -> SourcePlaylist:
        url = url.strip()
        if not self._is_explicit_source_playlist_url(url):
            return SourcePlaylist(
                url=url,
                error="Enter an explicit Source Playlist URL to preview.",
            )
        try:
            return self.extractor.preview(url)
        except Exception as exc:
            return SourcePlaylist(url=url, error=str(exc))

    def transfer_selected(
        self, entries: list[SourcePlaylistEntry], headphones_folder: Path
    ) -> OperationResult:
        outcomes: list[TransferOutcome] = []
        for entry in entries:
            if not entry.available:
                outcomes.append(
                    TransferOutcome(
                        status=TransferStatus.ERROR,
                        title=entry.title,
                        source_url=entry.url,
                        message=entry.error or "Source Playlist entry is unavailable.",
                    )
                )
                continue
            if not self._is_supported_public_url(entry.url):
                outcomes.append(
                    TransferOutcome(
                        status=TransferStatus.ERROR,
                        title=entry.title,
                        source_url=entry.url,
                        message="Source Playlist entry URL is not supported.",
                    )
                )
                continue
            outcomes.append(self._transfer_url(entry.url, headphones_folder, title_hint=entry.title))
        return OperationResult(outcomes)

    def _transfer_url(
        self, url: str, headphones_folder: Path, title_hint: str | None = None
    ) -> TransferOutcome:
        try:
            self.filesystem.ensure_headphones_folder(headphones_folder)
        except Exception as exc:
            return TransferOutcome(
                status=TransferStatus.ERROR,
                title=title_hint,
                source_url=url,
                message=str(exc),
            )

        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
            source_item, extracted_path = self.extractor.extract(url, self.workspace / "extracted")
            filename = self.filesystem.safe_audio_filename(source_item.title)
            audio_path = self.workspace / "audio" / filename
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            media_info = self.encoder.encode_mp3(extracted_path, audio_path)
            if not media_info.is_stereo_192kbps_mp3:
                return TransferOutcome(
                    status=TransferStatus.ERROR,
                    title=source_item.title,
                    source_url=url,
                    message="Audio File is not a stereo 192 kbps MP3.",
                )
            probed_info = self.encoder.probe(audio_path)
            if not probed_info.is_stereo_192kbps_mp3:
                return TransferOutcome(
                    status=TransferStatus.ERROR,
                    title=source_item.title,
                    source_url=url,
                    message="Encoded Audio File failed artifact verification.",
                )
            transfer_path = self.filesystem.transfer_verified(audio_path, headphones_folder, filename)
            return TransferOutcome(
                status=TransferStatus.TRANSFERRED,
                title=source_item.title,
                source_url=url,
                transfer_path=transfer_path,
                message="Transferred",
            )
        except Exception as exc:
            title = locals().get("source_item").title if "source_item" in locals() else None
            if "filename" in locals():
                self._remove_incomplete_transfer_artifacts(headphones_folder, locals()["filename"])
            return TransferOutcome(
                status=TransferStatus.ERROR,
                title=title or title_hint,
                source_url=url,
                message=str(exc),
            )

    def _is_supported_public_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        host = parsed.hostname.lower() if parsed.hostname else ""
        if host == "youtu.be":
            return bool(parsed.path.strip("/"))
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            return parsed.path == "/watch" and bool(parse_qs(parsed.query).get("v"))
        return False

    def _is_explicit_source_playlist_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = parsed.hostname.lower() if parsed.hostname else ""
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            return parsed.path == "/playlist" and bool(parse_qs(parsed.query).get("list"))
        return False

    def _remove_incomplete_transfer_artifacts(self, headphones_folder: Path, filename: str) -> None:
        for path in (
            headphones_folder / filename,
            headphones_folder / f"{filename}.incomplete",
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
