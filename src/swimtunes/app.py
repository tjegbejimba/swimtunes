from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from swimtunes.adapters.ffmpeg import FfmpegMediaAdapter
from swimtunes.adapters.filesystem import LocalFilesystemAdapter
from swimtunes.adapters.ytdlp import YtDlpExtractor
from swimtunes.models import OperationResult, SourceItem, TransferOutcome, TransferRecord, TransferStatus
from swimtunes.paths import default_workspace
from swimtunes.ports import Encoder, Extractor, FileSystem, TransferStateStore
from swimtunes.state import JsonTransferStateStore


class Workflow:
    def __init__(
        self,
        extractor: Extractor | None = None,
        encoder: Encoder | None = None,
        filesystem: FileSystem | None = None,
        workspace: Path | None = None,
        state_store: TransferStateStore | None = None,
    ) -> None:
        self.extractor = extractor or YtDlpExtractor()
        self.encoder = encoder or FfmpegMediaAdapter()
        self.filesystem = filesystem or LocalFilesystemAdapter()
        self.workspace = workspace or default_workspace()
        self.state_store = state_store or JsonTransferStateStore(self.workspace / "transfer-state.json")

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
            source_item = self.extractor.resolve(url)
            existing = self._existing_record_outcome(source_item, headphones_folder)
            if existing is not None:
                return OperationResult([existing])

            source_item, extracted_path = self.extractor.extract(url, self.workspace / "extracted")
            filename = self._transfer_filename(source_item, headphones_folder)
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
            self.state_store.save(
                TransferRecord(
                    source_id=source_item.source_id,
                    title=source_item.title,
                    filename=transfer_path.name,
                    byte_size=audio_path.stat().st_size,
                    sha256=self._sha256(audio_path),
                )
            )
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
            if "filename" in locals():
                self._remove_incomplete_transfer_artifacts(headphones_folder, locals()["filename"])
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
        host = parsed.hostname.lower() if parsed.hostname else ""
        if host == "youtu.be":
            return bool(parsed.path.strip("/"))
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            return parsed.path == "/watch" and bool(parse_qs(parsed.query).get("v"))
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

    def _existing_record_outcome(
        self,
        source_item: SourceItem,
        headphones_folder: Path,
    ) -> TransferOutcome | None:
        record = self.state_store.get(source_item.source_id)
        if record is None:
            return None
        transfer_path = headphones_folder / record.filename
        if (
            transfer_path.exists()
            and transfer_path.stat().st_size == record.byte_size
            and self._sha256(transfer_path) == record.sha256
        ):
            return TransferOutcome(
                status=TransferStatus.ALREADY_PRESENT,
                title=source_item.title,
                source_url=source_item.url,
                transfer_path=transfer_path,
                message="Already present",
            )
        if transfer_path.exists():
            return TransferOutcome(
                status=TransferStatus.ERROR,
                title=source_item.title,
                source_url=source_item.url,
                transfer_path=transfer_path,
                message="Existing Transfer appears damaged; repair is required before retry.",
            )
        return None

    def _transfer_filename(self, source_item: SourceItem, headphones_folder: Path) -> str:
        recorded = self.state_store.get(source_item.source_id)
        if recorded is not None:
            return recorded.filename

        safe = self.filesystem.safe_audio_filename(source_item.title)
        suffix = hashlib.sha256(source_item.source_id.encode("utf-8")).hexdigest()[:8]
        if safe.lower().endswith(".mp3"):
            stem = safe[:-4]
            extension = ".mp3"
        else:
            stem = safe
            extension = ".mp3"
        candidate = f"{stem}--{suffix}{extension}"
        known_owned = {record.filename for record in self.state_store.all()}
        if candidate not in known_owned and not (headphones_folder / candidate).exists():
            return candidate
        counter = 2
        while True:
            next_candidate = f"{stem}--{suffix}-{counter}{extension}"
            if next_candidate not in known_owned and not (headphones_folder / next_candidate).exists():
                return next_candidate
            counter += 1

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
