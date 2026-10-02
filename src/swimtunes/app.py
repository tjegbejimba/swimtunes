from __future__ import annotations

from collections.abc import Callable, Iterable
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from swimtunes.adapters.device_identity import platform_device_identity_adapter
from swimtunes.adapters.ffmpeg import FfmpegMediaAdapter
from swimtunes.adapters.filesystem import LocalFilesystemAdapter
from swimtunes.adapters.settings import JsonSettingsStore
from swimtunes.adapters.ytdlp import YtDlpExtractor
from swimtunes.models import (
    HeadphonesFolderSelection,
    OperationResult,
    SourceItem,
    SourcePlaylist,
    SourcePlaylistEntry,
    TransferOutcome,
    TransferRecord,
    TransferStatus,
)
from swimtunes.paths import default_workspace
from swimtunes.ports import DeviceIdentity, Encoder, Extractor, FileSystem, SettingsStore, TransferStateStore
from swimtunes.state import JsonTransferStateStore

ProgressCallback = Callable[[TransferOutcome], None]
SourceInput = str | SourceItem | SourcePlaylistEntry
PENDING_TRANSFER = "pending"
COMPLETED_TRANSFER = "completed"


class Workflow:
    def __init__(
        self,
        extractor: Extractor | None = None,
        encoder: Encoder | None = None,
        filesystem: FileSystem | None = None,
        settings: SettingsStore | None = None,
        device_identity: DeviceIdentity | None = None,
        workspace: Path | None = None,
        state_store: TransferStateStore | None = None,
    ) -> None:
        self.extractor = extractor or YtDlpExtractor()
        self.encoder = encoder or FfmpegMediaAdapter()
        self.filesystem = filesystem or LocalFilesystemAdapter()
        self.settings = settings or JsonSettingsStore()
        self.device_identity = device_identity or platform_device_identity_adapter()
        self.workspace = workspace or default_workspace()
        self.state_store = state_store or JsonTransferStateStore(self.workspace / "transfer-state.json")

    def choose_headphones_folder(self, headphones_folder: Path) -> OperationResult:
        try:
            self.filesystem.ensure_headphones_folder(headphones_folder)
            identity = self.device_identity.identity_for(headphones_folder)
            if identity is None:
                raise ValueError(
                    "Headphones Device identity could not be verified; reselect a "
                    "verifiable Headphones Folder before transferring."
                )
            self.settings.save_headphones_folder(
                HeadphonesFolderSelection(path=headphones_folder, device_identity=identity)
            )
            return OperationResult([
                TransferOutcome(
                    status=TransferStatus.TRANSFERRED,
                    transfer_path=headphones_folder,
                    message="Headphones Folder selected.",
                )
            ])
        except Exception as exc:
            return OperationResult([TransferOutcome(status=TransferStatus.ERROR, message=str(exc))])

    def transfer_one(self, url: str, headphones_folder: Path | None = None) -> OperationResult:
        return self.run([url], headphones_folder)

    def pending_transfers(self) -> list[TransferRecord]:
        return [record for record in self.state_store.all() if record.status == PENDING_TRANSFER]

    def retry_pending(
        self,
        source_id: str,
        headphones_folder: Path | None = None,
        on_progress: ProgressCallback | None = None,
        confirm_repair: bool = False,
    ) -> OperationResult:
        record = self.state_store.get(source_id)
        if record is None or record.status != PENDING_TRANSFER:
            return OperationResult(
                [
                    TransferOutcome(
                        status=TransferStatus.ERROR,
                        message="No pending Transfer is available to retry.",
                    )
                ]
            )
        try:
            resolved_headphones_folder = self._resolve_headphones_folder(headphones_folder)
            self.filesystem.ensure_headphones_folder(resolved_headphones_folder)
        except Exception as exc:
            return OperationResult(
                [
                    TransferOutcome(
                        status=TransferStatus.ERROR,
                        title=record.title,
                        source_url=record.source_url or None,
                        message=str(exc),
                    )
                ]
            )
        return OperationResult(
            [
                self._transfer_recovered_audio(
                    record,
                    resolved_headphones_folder,
                    on_progress=on_progress,
                    confirm_repair=confirm_repair,
                )
            ]
        )

    def run(
        self,
        items: Iterable[SourceInput],
        headphones_folder: Path | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> OperationResult:
        outcomes: list[TransferOutcome] = []
        for item in items:
            outcomes.append(self._process_one(item, headphones_folder, on_progress))
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
        self,
        entries: list[SourcePlaylistEntry],
        headphones_folder: Path | None = None,
        on_progress: ProgressCallback | None = None,
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
            outcomes.extend(self.run([entry], headphones_folder, on_progress).outcomes)
        return OperationResult(outcomes)

    def transfer_many(self, urls: list[str], headphones_folder: Path | None = None) -> OperationResult:
        return self.run(urls, headphones_folder)

    def _process_one(
        self,
        item: SourceInput,
        headphones_folder: Path | None,
        on_progress: ProgressCallback | None,
    ) -> TransferOutcome:
        url = self._source_url(item).strip()
        title_hint = self._source_title(item)
        if not self._is_supported_public_url(url):
            return TransferOutcome(
                status=TransferStatus.ERROR,
                title=title_hint,
                source_url=url,
                message="Enter a public YouTube or SoundCloud URL.",
            )

        try:
            resolved_headphones_folder = self._resolve_headphones_folder(headphones_folder)
            self.filesystem.ensure_headphones_folder(resolved_headphones_folder)
        except Exception as exc:
            return TransferOutcome(
                status=TransferStatus.ERROR,
                title=title_hint,
                source_url=url,
                message=str(exc),
            )

        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
            source_item = self._resolve_source_item(item, url)
            existing = self._existing_record_outcome(
                source_item,
                resolved_headphones_folder,
                on_progress=on_progress,
                confirm_repair=False,
            )
            if existing is not None:
                return existing

            extracted_item, extracted_path = self.extractor.extract(url, self.workspace / "extracted")
            source_item = SourceItem(
                source_id=source_item.source_id,
                url=url,
                title=extracted_item.title or source_item.title,
            )
            filename = self._transfer_filename(source_item, resolved_headphones_folder)
            audio_path = self._recovery_audio_path(filename)
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
                    audio_path=audio_path,
                    message="Encoded Audio File failed artifact verification.",
                )
            downloaded = TransferOutcome(
                status=TransferStatus.DOWNLOADED,
                title=source_item.title,
                source_url=url,
                audio_path=audio_path,
                message="Downloaded Audio File",
            )
            if on_progress is not None:
                on_progress(downloaded)
            self.state_store.save(
                TransferRecord(
                    source_id=source_item.source_id,
                    title=source_item.title,
                    filename=filename,
                    byte_size=audio_path.stat().st_size,
                    sha256=self._sha256(audio_path),
                    source_url=url,
                    status=PENDING_TRANSFER,
                )
            )
            transfer_path = self.filesystem.transfer_verified(audio_path, resolved_headphones_folder, filename)
            self.state_store.save(
                TransferRecord(
                    source_id=source_item.source_id,
                    title=source_item.title,
                    filename=transfer_path.name,
                    byte_size=audio_path.stat().st_size,
                    sha256=self._sha256(audio_path),
                    source_url=url,
                    status=COMPLETED_TRANSFER,
                )
            )
            transferred = TransferOutcome(
                status=TransferStatus.TRANSFERRED,
                title=source_item.title,
                source_url=url,
                audio_path=audio_path,
                transfer_path=transfer_path,
                message="Transferred",
            )
            if on_progress is not None:
                on_progress(transferred)
            return transferred
        except Exception as exc:
            title = locals().get("source_item").title if "source_item" in locals() else title_hint
            if "filename" in locals():
                self._remove_incomplete_transfer_artifacts(resolved_headphones_folder, locals()["filename"])
            return TransferOutcome(
                status=TransferStatus.ERROR,
                title=title,
                source_url=url,
                audio_path=locals().get("audio_path"),
                message=str(exc),
            )

    def _source_url(self, item: SourceInput) -> str:
        if isinstance(item, SourceItem | SourcePlaylistEntry):
            return item.url
        return item

    def _source_title(self, item: SourceInput) -> str | None:
        if isinstance(item, SourceItem | SourcePlaylistEntry):
            return item.title
        return None

    def _resolve_source_item(self, item: SourceInput, url: str) -> SourceItem:
        if isinstance(item, SourceItem):
            return item
        if isinstance(item, SourcePlaylistEntry):
            return SourceItem(source_id=item.source_id, url=item.url, title=item.title)
        return self.extractor.resolve(url)

    def _is_supported_public_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        host = parsed.hostname.lower() if parsed.hostname else ""
        if host == "youtu.be":
            return bool(parsed.path.strip("/"))
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            return parsed.path == "/watch" and bool(parse_qs(parsed.query).get("v"))
        if host in {"soundcloud.com", "www.soundcloud.com"}:
            parts = [part for part in parsed.path.split("/") if part]
            return len(parts) >= 2 and parts[0] != "sets"
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

    def _resolve_headphones_folder(self, requested_folder: Path | None) -> Path:
        selection = self.settings.load_headphones_folder()
        if requested_folder is not None:
            if selection is None:
                return requested_folder
            if selection.path != requested_folder:
                raise ValueError("Reselect this Headphones Folder in settings before transferring.")
            return self._verified_or_reconciled_folder(selection)

        if selection is None:
            raise ValueError("Choose a Headphones Folder before starting.")
        return self._verified_or_reconciled_folder(selection)

    def _verified_or_reconciled_folder(self, selection: HeadphonesFolderSelection) -> Path:
        current_identity = self.device_identity.identity_for(selection.path)
        if current_identity == selection.device_identity:
            return selection.path

        reconciled = self.device_identity.find_folder(selection.device_identity, selection.path)
        if reconciled is not None:
            reconciled_selection = HeadphonesFolderSelection(
                path=reconciled,
                device_identity=selection.device_identity,
            )
            self.settings.save_headphones_folder(reconciled_selection)
            return reconciled

        raise ValueError(
            "Headphones Device identity could not be verified; reselect the "
            "Headphones Folder before transferring."
        )

    def _existing_record_outcome(
        self,
        source_item: SourceItem,
        headphones_folder: Path,
        on_progress: ProgressCallback | None,
        confirm_repair: bool,
    ) -> TransferOutcome | None:
        record = self.state_store.get(source_item.source_id)
        if record is None:
            return None
        if record.status == PENDING_TRANSFER:
            return self._transfer_recovered_audio(
                record,
                headphones_folder,
                on_progress=on_progress,
                confirm_repair=confirm_repair,
            )
        transfer_path = headphones_folder / record.filename
        if self._matches_record(transfer_path, record):
            return TransferOutcome(
                status=TransferStatus.ALREADY_PRESENT,
                title=source_item.title,
                source_url=source_item.url,
                transfer_path=transfer_path,
                message="Already present",
            )
        if transfer_path.exists():
            return TransferOutcome(
                status=TransferStatus.REPAIR_NEEDED,
                title=source_item.title,
                source_url=source_item.url,
                transfer_path=transfer_path,
                message="Existing Transfer appears damaged; repair requires confirmation before retry.",
            )
        return None

    def _transfer_recovered_audio(
        self,
        record: TransferRecord,
        headphones_folder: Path,
        on_progress: ProgressCallback | None,
        confirm_repair: bool,
    ) -> TransferOutcome:
        audio_path = self._recovery_audio_path(record.filename)
        transfer_path = headphones_folder / record.filename
        if not self._matches_record(audio_path, record):
            return TransferOutcome(
                status=TransferStatus.ERROR,
                title=record.title,
                source_url=record.source_url or None,
                audio_path=audio_path,
                transfer_path=transfer_path,
                message="Pending Audio File is missing or damaged; the Source Item must be downloaded again.",
            )
        if self._matches_record(transfer_path, record):
            self._save_completed_record(record, transfer_path)
            return TransferOutcome(
                status=TransferStatus.ALREADY_PRESENT,
                title=record.title,
                source_url=record.source_url or None,
                audio_path=audio_path,
                transfer_path=transfer_path,
                message="Already present",
            )
        if transfer_path.exists() and not confirm_repair:
            return TransferOutcome(
                status=TransferStatus.REPAIR_NEEDED,
                title=record.title,
                source_url=record.source_url or None,
                audio_path=audio_path,
                transfer_path=transfer_path,
                message="Damaged app-owned copy requires confirmation before repair.",
            )
        downloaded = TransferOutcome(
            status=TransferStatus.DOWNLOADED,
            title=record.title,
            source_url=record.source_url or None,
            audio_path=audio_path,
            message="Downloaded Audio File",
        )
        if on_progress is not None:
            on_progress(downloaded)
        try:
            completed_path = self.filesystem.transfer_verified(audio_path, headphones_folder, record.filename)
            self._save_completed_record(record, completed_path)
            transferred = TransferOutcome(
                status=TransferStatus.TRANSFERRED,
                title=record.title,
                source_url=record.source_url or None,
                audio_path=audio_path,
                transfer_path=completed_path,
                message="Transferred",
            )
            if on_progress is not None:
                on_progress(transferred)
            return transferred
        except Exception as exc:
            self._remove_incomplete_transfer_artifacts(headphones_folder, record.filename)
            return TransferOutcome(
                status=TransferStatus.ERROR,
                title=record.title,
                source_url=record.source_url or None,
                audio_path=audio_path,
                transfer_path=transfer_path,
                message=str(exc),
            )

    def _save_completed_record(self, record: TransferRecord, transfer_path: Path) -> None:
        self.state_store.save(
            TransferRecord(
                source_id=record.source_id,
                title=record.title,
                filename=transfer_path.name,
                byte_size=record.byte_size,
                sha256=record.sha256,
                source_url=record.source_url,
                status=COMPLETED_TRANSFER,
            )
        )

    def _recovery_audio_path(self, filename: str) -> Path:
        return self.workspace / "recovery" / filename

    def _matches_record(self, path: Path, record: TransferRecord) -> bool:
        return path.exists() and path.stat().st_size == record.byte_size and self._sha256(path) == record.sha256

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
