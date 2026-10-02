from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from swimtunes.adapters.device_identity import platform_device_identity_adapter
from swimtunes.adapters.ffmpeg import FfmpegMediaAdapter
from swimtunes.adapters.filesystem import LocalFilesystemAdapter
from swimtunes.adapters.settings import JsonSettingsStore
from swimtunes.adapters.ytdlp import YtDlpExtractor
from swimtunes.models import HeadphonesFolderSelection, OperationResult, TransferOutcome, TransferStatus
from swimtunes.paths import default_workspace
from swimtunes.ports import DeviceIdentity, Encoder, Extractor, FileSystem, SettingsStore


class Workflow:
    def __init__(
        self,
        extractor: Extractor | None = None,
        encoder: Encoder | None = None,
        filesystem: FileSystem | None = None,
        settings: SettingsStore | None = None,
        device_identity: DeviceIdentity | None = None,
        workspace: Path | None = None,
    ) -> None:
        self.extractor = extractor or YtDlpExtractor()
        self.encoder = encoder or FfmpegMediaAdapter()
        self.filesystem = filesystem or LocalFilesystemAdapter()
        self.settings = settings or JsonSettingsStore()
        self.device_identity = device_identity or platform_device_identity_adapter()
        self.workspace = workspace or default_workspace()

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
            return OperationResult([
                TransferOutcome(status=TransferStatus.ERROR, message=str(exc))
            ])

    def transfer_one(self, url: str, headphones_folder: Path | None = None) -> OperationResult:
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
            headphones_folder = self._resolve_headphones_folder(headphones_folder)
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

    def _resolve_headphones_folder(self, requested_folder: Path | None) -> Path:
        selection = self.settings.load_headphones_folder()
        if requested_folder is not None:
            if selection is None:
                return requested_folder
            if selection.path != requested_folder:
                raise ValueError(
                    "Reselect this Headphones Folder in settings before transferring."
                )
            return self._verified_or_reconciled_folder(selection)

        if selection is None:
            raise ValueError("Choose a Headphones Folder before starting.")
        return self._verified_or_reconciled_folder(selection)

    def _verified_or_reconciled_folder(self, selection: HeadphonesFolderSelection) -> Path:
        current_identity = self.device_identity.identity_for(selection.path)
        if current_identity == selection.device_identity:
            return selection.path

        reconciled = self.device_identity.find_folder(
            selection.device_identity,
            selection.path,
        )
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
