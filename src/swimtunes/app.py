from __future__ import annotations

from collections.abc import Callable, Iterable
import hashlib
import inspect
import math
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from swimtunes.adapters.device_identity import platform_device_identity_adapter
from swimtunes.adapters.ffmpeg import FfmpegMediaAdapter
from swimtunes.adapters.filesystem import LocalFilesystemAdapter
from swimtunes.adapters.settings import JsonSettingsStore
from swimtunes.adapters.ytdlp import YtDlpExtractor
from swimtunes.models import (
    HeadphonesFolderSelection,
    LocalAudioFile,
    LocalLibrarySettings,
    OperationResult,
    SelectionSpaceSummary,
    SourceItem,
    SourcePlaylist,
    SourcePlaylistEntry,
    TransferOutcome,
    TransferRecord,
    TransferStatus,
)
from swimtunes.paths import default_workspace
from swimtunes.ports import (
    CancelToken,
    DeviceIdentity,
    Encoder,
    Extractor,
    FileSystem,
    OperationCancelled,
    SettingsStore,
    TransferStateStore,
)
from swimtunes.state import JsonTransferStateStore

ProgressCallback = Callable[[TransferOutcome], None]
SourceInput = str | SourceItem | SourcePlaylistEntry
PENDING_TRANSFER = "pending"
COMPLETED_TRANSFER = "completed"
LOCAL_TRANSFER = "local"
ESTIMATED_AUDIO_BYTES_PER_SECOND = 24_000
ESTIMATE_MARGIN_RATIO = 0.05


class SimpleCancelToken:
    def __init__(self) -> None:
        self._cancelled = False

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


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
        self._resolved_metadata_cache: dict[str, SourceItem] = {}

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

    def local_library_settings(self) -> LocalLibrarySettings:
        return self.settings.load_local_library()

    def configure_local_library(
        self,
        enabled: bool,
        local_library: Path | None,
        headphones_folder: Path | None = None,
    ) -> OperationResult:
        try:
            if not enabled:
                self.settings.save_local_library(LocalLibrarySettings(enabled=False, path=None))
                return OperationResult(
                    [TransferOutcome(status=TransferStatus.TRANSFERRED, message="Local Library disabled.")]
                )
            if local_library is None:
                raise ValueError("Choose a Local Library before enabling keep local MP3s.")
            self._ensure_local_library(local_library)
            selected_headphones = headphones_folder
            if selected_headphones is None and (selection := self.settings.load_headphones_folder()) is not None:
                selected_headphones = selection.path
            if selected_headphones is not None and local_library.resolve() == selected_headphones.resolve():
                raise ValueError("Choose a Local Library separate from the Headphones Folder.")
            self.settings.save_local_library(LocalLibrarySettings(enabled=True, path=local_library))
            return OperationResult(
                [TransferOutcome(status=TransferStatus.TRANSFERRED, message="Local Library settings saved.")]
            )
        except Exception as exc:
            return OperationResult([TransferOutcome(status=TransferStatus.ERROR, message=str(exc))])

    def local_audio_files(self) -> list[LocalAudioFile]:
        settings = self.settings.load_local_library()
        if not settings.enabled or settings.path is None or not settings.path.exists():
            return []
        files: list[LocalAudioFile] = []
        for record in self.state_store.all():
            audio_path = settings.path / record.filename
            if record.status in {LOCAL_TRANSFER, COMPLETED_TRANSFER} and self._matches_record(audio_path, record):
                files.append(
                    LocalAudioFile(
                        source_id=record.source_id,
                        title=record.title,
                        source_url=record.source_url or None,
                        path=audio_path,
                        byte_size=record.byte_size,
                        sha256=record.sha256,
                    )
                )
        return files

    def transfer_local_audio(
        self,
        source_id: str,
        headphones_folder: Path | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> OperationResult:
        record = self.state_store.get(source_id)
        if record is None:
            return OperationResult(
                [TransferOutcome(status=TransferStatus.ERROR, message="No Local Library Audio File is available.")]
            )
        try:
            local_library = self._require_local_library()
            audio_path = local_library / record.filename
            if not self._matches_record(audio_path, record):
                raise ValueError("Local Library Audio File is missing or damaged.")
            resolved_headphones_folder = self._resolve_headphones_folder(headphones_folder)
            self.filesystem.ensure_headphones_folder(resolved_headphones_folder)
            return OperationResult(
                [
                    self._transfer_audio_record(
                        record,
                        audio_path,
                        resolved_headphones_folder,
                        on_progress=on_progress,
                    )
                ]
            )
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
        cancel_token: CancelToken | None = None,
    ) -> OperationResult:
        outcomes: list[TransferOutcome] = []
        raw_items = list(items)
        if self._is_cancelled(cancel_token):
            return OperationResult([self._cancelled_outcome(raw_items[0])] if raw_items else [])
        item_list = self._items_with_metadata(raw_items)
        summary = self._selection_space_summary_for_resolved_items(item_list, headphones_folder)
        if summary.known_too_large:
            return OperationResult(
                [
                    TransferOutcome(
                        status=TransferStatus.ERROR,
                        message=(
                            "Selected Source Items are estimated to require "
                            f"{summary.estimated_bytes} bytes, but only "
                            f"{summary.free_bytes} bytes are available."
                        ),
                    )
                ]
            )
        for item in item_list:
            if self._is_cancelled(cancel_token):
                outcomes.append(self._cancelled_outcome(item))
                break
            outcome = self._process_one(item, headphones_folder, on_progress, cancel_token)
            outcomes.append(outcome)
            if outcome.status is TransferStatus.CANCELLED:
                break
        return OperationResult(outcomes)

    def selection_space_summary(
        self,
        items: Iterable[SourceInput],
        headphones_folder: Path | None = None,
    ) -> SelectionSpaceSummary:
        item_list = list(items)
        item_list = self._items_with_metadata(item_list)
        return self._selection_space_summary_for_resolved_items(item_list, headphones_folder)

    def _selection_space_summary_for_resolved_items(
        self,
        item_list: list[SourceInput],
        headphones_folder: Path | None = None,
    ) -> SelectionSpaceSummary:
        free_bytes = self._free_bytes(headphones_folder)
        known_total = 0
        unknown_count = 0
        for item in item_list:
            duration = self._known_duration_seconds(item)
            if duration is None:
                unknown_count += 1
            else:
                known_total += estimate_audio_size_bytes(duration)
        estimated_bytes = known_total if unknown_count == 0 else None
        return SelectionSpaceSummary(
            item_count=len(item_list),
            estimated_bytes=estimated_bytes,
            free_bytes=free_bytes,
            unknown_size_count=unknown_count,
        )

    def _items_with_metadata(self, items: list[SourceInput]) -> list[SourceInput]:
        resolved_items: list[SourceInput] = []
        for item in items:
            if isinstance(item, str) and self._is_supported_public_url(item.strip()):
                if item.strip() in self._resolved_metadata_cache:
                    resolved_items.append(self._resolved_metadata_cache[item.strip()])
                    continue
                try:
                    resolved = self.extractor.resolve(item.strip())
                    self._resolved_metadata_cache[item.strip()] = resolved
                    resolved_items.append(resolved)
                    continue
                except Exception:
                    pass
            resolved_items.append(item)
        return resolved_items

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
        cancel_token: CancelToken | None = None,
    ) -> OperationResult:
        outcomes: list[TransferOutcome] = []
        summary = self.selection_space_summary(
            [entry for entry in entries if entry.available],
            headphones_folder,
        )
        if summary.known_too_large:
            return OperationResult(
                [
                    TransferOutcome(
                        status=TransferStatus.ERROR,
                        message=(
                            "Selected Source Items are estimated to require "
                            f"{summary.estimated_bytes} bytes, but only "
                            f"{summary.free_bytes} bytes are available."
                        ),
                    )
                ]
            )
        for entry in entries:
            if self._is_cancelled(cancel_token):
                outcomes.append(self._cancelled_outcome(entry))
                break
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
            outcome = self._process_one(entry, headphones_folder, on_progress, cancel_token)
            outcomes.append(outcome)
            if outcome.status is TransferStatus.CANCELLED:
                break
        return OperationResult(outcomes)

    def transfer_many(self, urls: list[str], headphones_folder: Path | None = None) -> OperationResult:
        return self.run(urls, headphones_folder)

    def _is_cancelled(self, cancel_token: CancelToken | None) -> bool:
        return bool(cancel_token is not None and cancel_token.is_cancelled)

    def _cancelled_outcome(self, item: SourceInput, audio_path: Path | None = None) -> TransferOutcome:
        return TransferOutcome(
            status=TransferStatus.CANCELLED,
            title=self._source_title(item),
            source_url=self._source_url(item).strip(),
            audio_path=audio_path,
            message="Cancelled.",
        )

    def _extract(
        self,
        url: str,
        work_dir: Path,
        cancel_token: CancelToken | None,
    ) -> tuple[SourceItem, Path]:
        extract = self.extractor.extract
        if cancel_token is not None and "cancel_token" in inspect.signature(extract).parameters:
            return extract(url, work_dir, cancel_token=cancel_token)
        return extract(url, work_dir)

    def _transfer_verified(
        self,
        audio_path: Path,
        headphones_folder: Path,
        filename: str,
        cancel_token: CancelToken | None,
    ) -> Path:
        transfer_verified = self.filesystem.transfer_verified
        if cancel_token is not None and "cancel_token" in inspect.signature(transfer_verified).parameters:
            return transfer_verified(audio_path, headphones_folder, filename, cancel_token=cancel_token)
        return transfer_verified(audio_path, headphones_folder, filename)

    def _free_bytes(self, headphones_folder: Path | None) -> int | None:
        try:
            folder = self._resolve_headphones_folder(headphones_folder)
            free_bytes = getattr(self.filesystem, "free_bytes", None)
            return free_bytes(folder) if free_bytes is not None else None
        except Exception:
            return None

    def _known_duration_seconds(self, item: SourceInput) -> float | None:
        if isinstance(item, SourceItem | SourcePlaylistEntry):
            return item.duration_seconds
        return None

    def _process_one(
        self,
        item: SourceInput,
        headphones_folder: Path | None,
        on_progress: ProgressCallback | None,
        cancel_token: CancelToken | None,
    ) -> TransferOutcome:
        local_settings = self.settings.load_local_library()
        if local_settings.enabled:
            return self._process_one_keep_local(
                item,
                headphones_folder,
                on_progress,
                local_settings,
                cancel_token,
            )
        url = self._source_url(item).strip()
        title_hint = self._source_title(item)
        if self._is_cancelled(cancel_token):
            return self._cancelled_outcome(item)
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
            if self._is_cancelled(cancel_token):
                return self._cancelled_outcome(source_item)
            existing = self._existing_record_outcome(
                source_item,
                resolved_headphones_folder,
                on_progress=on_progress,
                confirm_repair=False,
            )
            if existing is not None:
                return existing

            extracted_item, extracted_path = self._extract(url, self.workspace / "extracted", cancel_token)
            if self._is_cancelled(cancel_token):
                return self._cancelled_outcome(source_item)
            source_item = SourceItem(
                source_id=source_item.source_id,
                url=url,
                title=extracted_item.title or source_item.title,
                duration_seconds=extracted_item.duration_seconds or source_item.duration_seconds,
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
            if self._is_cancelled(cancel_token):
                return self._cancelled_outcome(source_item, audio_path=audio_path)
            transfer_path = self._transfer_verified(
                audio_path,
                resolved_headphones_folder,
                filename,
                cancel_token,
            )
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
        except OperationCancelled as exc:
            title = locals().get("source_item").title if "source_item" in locals() else title_hint
            if "filename" in locals():
                self._remove_incomplete_transfer_artifacts(resolved_headphones_folder, locals()["filename"])
            return TransferOutcome(
                status=TransferStatus.CANCELLED,
                title=title,
                source_url=url,
                audio_path=locals().get("audio_path"),
                message=str(exc) or "Cancelled.",
            )
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

    def _process_one_keep_local(
        self,
        item: SourceInput,
        headphones_folder: Path | None,
        on_progress: ProgressCallback | None,
        local_settings: LocalLibrarySettings,
        cancel_token: CancelToken | None,
    ) -> TransferOutcome:
        url = self._source_url(item).strip()
        title_hint = self._source_title(item)
        if self._is_cancelled(cancel_token):
            return self._cancelled_outcome(item)
        if not self._is_supported_public_url(url):
            return TransferOutcome(
                status=TransferStatus.ERROR,
                title=title_hint,
                source_url=url,
                message="Enter a public YouTube or SoundCloud URL.",
            )

        try:
            local_library = self._require_local_library(local_settings)
            self.workspace.mkdir(parents=True, exist_ok=True)
            source_item = self._resolve_source_item(item, url)
            if self._is_cancelled(cancel_token):
                return self._cancelled_outcome(source_item)
            record = self.state_store.get(source_item.source_id)
            if record is not None:
                audio_path = local_library / record.filename
                if self._matches_record(audio_path, record):
                    try:
                        resolved_headphones_folder = self._resolve_headphones_folder(headphones_folder)
                        self.filesystem.ensure_headphones_folder(resolved_headphones_folder)
                    except Exception:
                        return TransferOutcome(
                            status=TransferStatus.DOWNLOADED,
                            title=record.title,
                            source_url=record.source_url or url,
                            audio_path=audio_path,
                            message="Downloaded Audio File",
                        )
                    return self._transfer_audio_record(
                        record,
                        audio_path,
                        resolved_headphones_folder,
                        on_progress=on_progress,
                        cancel_token=cancel_token,
                    )

            filename = self._transfer_filename(source_item, local_library)
            audio_path = local_library / filename
            extracted_item, extracted_path = self._extract(url, self.workspace / "extracted", cancel_token)
            if self._is_cancelled(cancel_token):
                return self._cancelled_outcome(source_item)
            source_item = SourceItem(
                source_id=source_item.source_id,
                url=url,
                title=extracted_item.title or source_item.title,
                duration_seconds=extracted_item.duration_seconds or source_item.duration_seconds,
            )
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
            record = TransferRecord(
                source_id=source_item.source_id,
                title=source_item.title,
                filename=filename,
                byte_size=audio_path.stat().st_size,
                sha256=self._sha256(audio_path),
                source_url=url,
                status=LOCAL_TRANSFER,
            )
            self.state_store.save(record)
            downloaded = TransferOutcome(
                status=TransferStatus.DOWNLOADED,
                title=source_item.title,
                source_url=url,
                audio_path=audio_path,
                message="Downloaded Audio File",
            )
            if on_progress is not None:
                on_progress(downloaded)
            if self._is_cancelled(cancel_token):
                return self._cancelled_outcome(source_item, audio_path=audio_path)
            try:
                resolved_headphones_folder = self._resolve_headphones_folder(headphones_folder)
                self.filesystem.ensure_headphones_folder(resolved_headphones_folder)
            except Exception:
                return downloaded
            return self._transfer_audio_record(
                record,
                audio_path,
                resolved_headphones_folder,
                on_progress=on_progress,
                cancel_token=cancel_token,
            )
        except OperationCancelled as exc:
            title = locals().get("source_item").title if "source_item" in locals() else title_hint
            if "resolved_headphones_folder" in locals() and "filename" in locals():
                self._remove_incomplete_transfer_artifacts(
                    locals()["resolved_headphones_folder"],
                    locals()["filename"],
                )
            if "resolved_headphones_folder" in locals() and "record" in locals():
                self._remove_incomplete_transfer_artifacts(
                    locals()["resolved_headphones_folder"],
                    locals()["record"].filename,
                )
            return TransferOutcome(
                status=TransferStatus.CANCELLED,
                title=title,
                source_url=url,
                audio_path=locals().get("audio_path"),
                message=str(exc) or "Cancelled.",
            )
        except Exception as exc:
            title = locals().get("source_item").title if "source_item" in locals() else title_hint
            if "resolved_headphones_folder" in locals() and "filename" in locals():
                self._remove_incomplete_transfer_artifacts(
                    locals()["resolved_headphones_folder"],
                    locals()["filename"],
                )
            if "resolved_headphones_folder" in locals() and "record" in locals():
                self._remove_incomplete_transfer_artifacts(
                    locals()["resolved_headphones_folder"],
                    locals()["record"].filename,
                )
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
            return SourceItem(
                source_id=item.source_id,
                url=item.url,
                title=item.title,
                duration_seconds=item.duration_seconds,
            )
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
            return len(parts) >= 2 and parts[1] != "sets"
        return False

    def _is_explicit_source_playlist_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = parsed.hostname.lower() if parsed.hostname else ""
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
            return parsed.path == "/playlist" and bool(parse_qs(parsed.query).get("list"))
        if host in {"soundcloud.com", "www.soundcloud.com"}:
            parts = [part for part in parsed.path.split("/") if part]
            return len(parts) >= 3 and parts[1] == "sets"
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

    def _transfer_audio_record(
        self,
        record: TransferRecord,
        audio_path: Path,
        headphones_folder: Path,
        on_progress: ProgressCallback | None,
        cancel_token: CancelToken | None = None,
    ) -> TransferOutcome:
        transfer_path = headphones_folder / record.filename
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
        transferred_path = self._transfer_verified(audio_path, headphones_folder, record.filename, cancel_token)
        self._save_completed_record(record, transferred_path)
        transferred = TransferOutcome(
            status=TransferStatus.TRANSFERRED,
            title=record.title,
            source_url=record.source_url or None,
            audio_path=audio_path,
            transfer_path=transferred_path,
            message="Transferred",
        )
        if on_progress is not None:
            on_progress(transferred)
        return transferred

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

    def _require_local_library(self, settings: LocalLibrarySettings | None = None) -> Path:
        settings = settings or self.settings.load_local_library()
        if not settings.enabled or settings.path is None:
            raise ValueError("Choose a Local Library before keeping local MP3s.")
        self._ensure_local_library(settings.path)
        return settings.path

    def _ensure_local_library(self, local_library: Path) -> None:
        if not local_library.exists() or not local_library.is_dir():
            raise ValueError("Choose an existing Local Library before keeping local MP3s.")
        probe = local_library / ".swimtunes-write-test"
        try:
            probe.write_bytes(b"")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            raise ValueError("Local Library is not writable.") from exc

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


def estimate_audio_size_bytes(duration_seconds: float) -> int:
    raw_bytes = max(0.0, duration_seconds) * ESTIMATED_AUDIO_BYTES_PER_SECOND
    return math.ceil(raw_bytes * (1 + ESTIMATE_MARGIN_RATIO))
