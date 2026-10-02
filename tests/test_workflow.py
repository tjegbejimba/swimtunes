import errno
from pathlib import Path

from swimtunes.app import Workflow
from swimtunes.adapters.settings import JsonSettingsStore
from swimtunes.models import HeadphonesDeviceIdentity, MediaInfo, SourceItem, TransferStatus
from swimtunes.ports import OperationCancelled
from swimtunes.state import JsonTransferStateStore


class RecordingExtractor:
    def __init__(
        self,
        source_media: Path,
        source_item: SourceItem | None = None,
        titles: dict[str, str] | None = None,
        durations: dict[str, float | None] | None = None,
    ) -> None:
        self.source_media = source_media
        self.source_item = source_item
        self.titles = titles or {}
        self.durations = durations or {}
        self.resolve_calls: list[str] = []
        self.calls: list[str] = []

    def resolve(self, url: str):
        self.resolve_calls.append(url)
        return self._item_for(url)

    def extract(self, url: str, work_dir: Path):
        self.calls.append(url)
        return self._item_for(url), self.source_media

    def _item_for(self, url: str) -> SourceItem:
        if self.source_item is not None:
            return SourceItem(source_id=self.source_item.source_id, url=url, title=self.source_item.title)
        title = self.titles.get(url, "Lane Mix")
        return SourceItem(source_id="yt:test-video", url=url, title=title, duration_seconds=self.durations.get(url))


class WritingEncoder:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def encode_mp3(self, source_media: Path, output_path: Path) -> MediaInfo:
        self.calls.append((source_media, output_path))
        output_path.write_bytes(b"verified mp3 payload")
        return MediaInfo(codec="mp3", channels=2, bit_rate=192_000, is_cbr=True)

    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo(codec="mp3", channels=2, bit_rate=192_000, is_cbr=True)


class FailingExtractor:
    def __init__(self, failed_urls: set[str] | None = None, source_media: Path | None = None) -> None:
        self.failed_urls = failed_urls
        self.source_media = source_media
        self.resolve_calls: list[str] = []
        self.calls: list[str] = []

    def resolve(self, url: str):
        self.resolve_calls.append(url)
        return SourceItem(source_id=f"source:{url}", url=url, title="Lane Mix")

    def extract(self, url: str, work_dir: Path):
        self.calls.append(url)
        if self.failed_urls is None or url in self.failed_urls:
            raise RuntimeError("public Source Item is unavailable")
        return SourceItem(source_id=f"source:{url}", url=url, title="Successful Track"), self.source_media


class FailingFilesystem:
    def ensure_headphones_folder(self, folder: Path) -> None:
        if not folder.exists():
            raise ValueError("Choose an existing Headphones Folder before starting.")

    def safe_audio_filename(self, title: str) -> str:
        return "Lane Mix.mp3"

    def transfer_verified(self, source: Path, headphones_folder: Path, filename: str) -> Path:
        (headphones_folder / filename).write_bytes(b"partial")
        (headphones_folder / f"{filename}.incomplete").write_bytes(b"partial")
        raise IOError("Transfer verification failed before completion.")


class SpaceExhaustedFilesystem(FailingFilesystem):
    def transfer_verified(self, source: Path, headphones_folder: Path, filename: str) -> Path:
        (headphones_folder / f"{filename}.incomplete").write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "No space left on Headphones Device")


class UnpluggedMidCopyFilesystem(FailingFilesystem):
    def transfer_verified(self, source: Path, headphones_folder: Path, filename: str) -> Path:
        (headphones_folder / f"{filename}.incomplete").write_bytes(b"partial")
        raise FileNotFoundError("Headphones Device disconnected during Transfer")


class RecordingFilesystem:
    def __init__(self, free_bytes: int | None = None) -> None:
        self.transfers: list[tuple[Path, Path, str]] = []
        self._free_bytes = free_bytes

    def ensure_headphones_folder(self, folder: Path) -> None:
        if not folder.exists():
            raise ValueError("Choose an existing Headphones Folder before starting.")

    def safe_audio_filename(self, title: str) -> str:
        return "Lane Mix.mp3"

    def transfer_verified(self, source: Path, headphones_folder: Path, filename: str) -> Path:
        self.transfers.append((source, headphones_folder, filename))
        destination = headphones_folder / filename
        destination.write_bytes(source.read_bytes())
        return destination

    def free_bytes(self, folder: Path) -> int | None:
        return self._free_bytes


class FakeDeviceIdentity:
    def __init__(self, identity: HeadphonesDeviceIdentity | None) -> None:
        self.identity = identity

    def identity_for(self, folder: Path):
        return self.identity

    def find_folder(self, expected_identity, remembered_folder):
        return remembered_folder if self.identity == expected_identity else None


def test_single_source_item_transfer_reports_success_after_verified_copy(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()

    workflow = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace",
    )

    result = workflow.transfer_one(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        headphones_folder,
    )

    assert result.ok is True
    assert len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert outcome.status is TransferStatus.TRANSFERRED
    assert outcome.title == "Lane Mix"
    assert outcome.transfer_path == headphones_folder / "Lane Mix--a63668cb.mp3"
    assert outcome.transfer_path.read_bytes() == b"verified mp3 payload"
    assert not (headphones_folder / "Lane Mix--a63668cb.mp3.incomplete").exists()


def test_absent_headphones_folder_prevents_extraction(artifact_dir: Path) -> None:
    extractor = RecordingExtractor(artifact_dir / "unused.webm")
    workflow = Workflow(
        extractor=extractor,
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace",
    )

    result = workflow.transfer_one(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        artifact_dir / "missing-headphones",
    )

    assert result.ok is False
    assert extractor.calls == []
    assert result.outcomes[0].status is TransferStatus.ERROR
    assert "Headphones Folder" in result.outcomes[0].message


def test_extraction_failure_surfaces_error_without_headphones_file(artifact_dir: Path) -> None:
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workflow = Workflow(
        extractor=FailingExtractor(),
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace",
    )

    result = workflow.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ", headphones_folder)

    assert result.ok is False
    assert result.outcomes[0].status is TransferStatus.ERROR
    assert "unavailable" in result.outcomes[0].message
    assert list(headphones_folder.iterdir()) == []


def test_copy_verification_failure_cleans_destination_artifacts(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workflow = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        filesystem=FailingFilesystem(),
        workspace=artifact_dir / "workspace",
    )

    result = workflow.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ", headphones_folder)

    assert result.ok is False
    assert result.outcomes[0].status is TransferStatus.ERROR
    assert "verification failed" in result.outcomes[0].message
    assert not (headphones_folder / "Lane Mix--a63668cb.mp3").exists()
    assert not (headphones_folder / "Lane Mix--a63668cb.mp3.incomplete").exists()


def test_unsupported_lookalike_url_is_rejected_before_extraction(artifact_dir: Path) -> None:
    extractor = RecordingExtractor(artifact_dir / "unused.webm")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workflow = Workflow(
        extractor=extractor,
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace",
    )

    result = workflow.transfer_one("https://youtube.com.evil.net/watch?v=dQw4w9WgXcQ", headphones_folder)

    assert result.ok is False
    assert extractor.calls == []
    assert result.outcomes[0].status is TransferStatus.ERROR


def test_default_workspace_uses_per_user_app_data_dir() -> None:
    workflow = Workflow()

    assert workflow.workspace != Path.cwd() / ".swimtunes-work"
    assert "SwimTunes" in workflow.workspace.parts


def test_batch_run_reports_each_source_item_independently(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=incidental"
    soundcloud_url = "https://soundcloud.com/swimmer/lane-mix"
    selected_source = SourceItem(
        source_id="soundcloud:lane-mix",
        url=soundcloud_url,
        title="Lane Mix",
    )
    bad_url = "https://example.com/not-supported"
    failing_url = "https://soundcloud.com/swimmer/removed-track"
    extractor = FailingExtractor(failed_urls={failing_url}, source_media=source_media)
    workflow = Workflow(
        extractor=extractor,
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace",
    )

    result = workflow.run([youtube_url, bad_url, selected_source, failing_url], headphones_folder)

    assert result.ok is False
    assert [outcome.status for outcome in result.outcomes] == [
        TransferStatus.TRANSFERRED,
        TransferStatus.ERROR,
        TransferStatus.TRANSFERRED,
        TransferStatus.ERROR,
    ]
    assert extractor.calls == [youtube_url, soundcloud_url, failing_url]
    assert "public YouTube or SoundCloud URL" in result.outcomes[1].message
    assert "unavailable" in result.outcomes[3].message


def test_batch_progress_distinguishes_downloaded_audio_file_from_transfer(
    artifact_dir: Path,
) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workflow = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace",
    )
    progress = []

    result = workflow.run(
        ["https://soundcloud.com/swimmer/lane-mix"],
        headphones_folder,
        on_progress=progress.append,
    )

    assert result.ok is True
    assert [event.status for event in progress] == [
        TransferStatus.DOWNLOADED,
        TransferStatus.TRANSFERRED,
    ]
    assert progress[0].message == "Downloaded Audio File"
    assert progress[0].audio_path == artifact_dir / "workspace" / "recovery" / "Lane Mix--a63668cb.mp3"
    assert progress[1].transfer_path == headphones_folder / "Lane Mix--a63668cb.mp3"


def test_repeat_source_id_is_skipped_after_restart_without_downloading_again(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    state_path = artifact_dir / "state" / "transfers.json"

    first_extractor = RecordingExtractor(source_media)
    first = Workflow(
        extractor=first_extractor,
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace-1",
        state_store=JsonTransferStateStore(state_path),
    )
    first_result = first.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ", headphones_folder)

    second_extractor = RecordingExtractor(
        source_media,
        SourceItem(
            source_id="yt:test-video",
            url="https://youtu.be/dQw4w9WgXcQ",
            title="Renamed Lane Mix",
        ),
    )
    second = Workflow(
        extractor=second_extractor,
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace-2",
        state_store=JsonTransferStateStore(state_path),
    )
    second_result = second.transfer_one("https://youtu.be/dQw4w9WgXcQ", headphones_folder)

    assert first_result.outcomes[0].status is TransferStatus.TRANSFERRED
    assert second_result.ok is True
    assert second_result.outcomes[0].status is TransferStatus.ALREADY_PRESENT
    assert second_result.outcomes[0].title == "Renamed Lane Mix"
    assert second_result.outcomes[0].transfer_path == first_result.outcomes[0].transfer_path
    assert second_extractor.resolve_calls == ["https://youtu.be/dQw4w9WgXcQ"]
    assert second_extractor.calls == []


def test_different_source_items_with_same_title_get_distinct_safe_filenames(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workflow = Workflow(
        extractor=RecordingExtractor(
            source_media,
            SourceItem(source_id="yt:first", url="https://youtu.be/first", title="../Lane:Mix?"),
        ),
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace",
        state_store=JsonTransferStateStore(artifact_dir / "state" / "transfers.json"),
    )

    first = workflow.transfer_one("https://youtu.be/first", headphones_folder)
    workflow.extractor = RecordingExtractor(
        source_media,
        SourceItem(source_id="yt:second", url="https://youtu.be/second", title="../Lane:Mix?"),
    )
    second = workflow.transfer_one("https://youtu.be/second", headphones_folder)

    assert first.outcomes[0].status is TransferStatus.TRANSFERRED
    assert second.outcomes[0].status is TransferStatus.TRANSFERRED
    assert first.outcomes[0].transfer_path != second.outcomes[0].transfer_path
    assert first.outcomes[0].transfer_path.name == "-Lane-Mix---ae363588.mp3"
    assert second.outcomes[0].transfer_path.name == "-Lane-Mix---29c66476.mp3"
    assert sorted(path.name for path in headphones_folder.iterdir()) == [
        "-Lane-Mix---29c66476.mp3",
        "-Lane-Mix---ae363588.mp3",
    ]


def test_existing_unknown_file_is_not_overwritten_or_counted_as_duplicate(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    unknown = headphones_folder / "Lane Mix--a63668cb.mp3"
    unknown.write_bytes(b"someone else's file")
    workflow = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace",
        state_store=JsonTransferStateStore(artifact_dir / "state" / "transfers.json"),
    )

    result = workflow.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ", headphones_folder)

    assert result.outcomes[0].status is TransferStatus.TRANSFERRED
    assert result.outcomes[0].transfer_path == headphones_folder / "Lane Mix--a63668cb-2.mp3"
    assert unknown.read_bytes() == b"someone else's file"


def test_corrupted_recorded_duplicate_reports_repair_needed_without_redownloading(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    state_path = artifact_dir / "state" / "transfers.json"
    workflow = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace-1",
        state_store=JsonTransferStateStore(state_path),
    )
    first = workflow.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ", headphones_folder)
    first.outcomes[0].transfer_path.write_bytes(b"damaged")
    extractor = RecordingExtractor(source_media)
    restarted = Workflow(
        extractor=extractor,
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace-2",
        state_store=JsonTransferStateStore(state_path),
    )

    result = restarted.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ", headphones_folder)

    assert result.ok is False
    assert result.outcomes[0].status is TransferStatus.REPAIR_NEEDED
    assert "repair" in result.outcomes[0].message
    assert extractor.calls == []


def test_failed_copy_keeps_pending_audio_file_for_restart_retry_without_redownloading(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workspace = artifact_dir / "workspace"
    state_path = workspace / "transfer-state.json"
    extractor = RecordingExtractor(source_media)
    first = Workflow(
        extractor=extractor,
        encoder=WritingEncoder(),
        filesystem=FailingFilesystem(),
        workspace=workspace,
        state_store=JsonTransferStateStore(state_path),
    )

    failed = first.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ", headphones_folder)

    assert failed.ok is False
    assert failed.outcomes[0].status is TransferStatus.ERROR
    pending = first.pending_transfers()
    assert len(pending) == 1
    assert pending[0].status == "pending"
    recovery_audio = workspace / "recovery" / pending[0].filename
    assert recovery_audio.read_bytes() == b"verified mp3 payload"
    assert not (headphones_folder / pending[0].filename).exists()
    assert not (headphones_folder / f"{pending[0].filename}.incomplete").exists()

    retry_filesystem = RecordingFilesystem()
    retry_extractor = RecordingExtractor(source_media)
    restarted = Workflow(
        extractor=retry_extractor,
        encoder=WritingEncoder(),
        filesystem=retry_filesystem,
        workspace=workspace,
        state_store=JsonTransferStateStore(state_path),
    )
    retried = restarted.retry_pending("yt:test-video", headphones_folder)

    assert retried.ok is True
    assert retried.outcomes[0].status is TransferStatus.TRANSFERRED
    assert retry_extractor.calls == []
    assert retry_filesystem.transfers == [(recovery_audio, headphones_folder, pending[0].filename)]
    assert restarted.pending_transfers() == []


def test_space_exhausted_copy_keeps_pending_audio_and_reports_no_success(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workspace = artifact_dir / "workspace"
    workflow = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        filesystem=SpaceExhaustedFilesystem(),
        workspace=workspace,
    )

    result = workflow.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ", headphones_folder)

    assert result.ok is False
    assert result.outcomes[0].status is TransferStatus.ERROR
    assert "space" in result.outcomes[0].message.lower() or "No space" in result.outcomes[0].message
    assert len(workflow.pending_transfers()) == 1
    pending = workflow.pending_transfers()[0]
    assert (workspace / "recovery" / pending.filename).exists()
    assert not (headphones_folder / pending.filename).exists()


def test_unplug_mid_copy_preserves_recovery_audio_for_later_retry(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workspace = artifact_dir / "workspace"
    workflow = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        filesystem=UnpluggedMidCopyFilesystem(),
        workspace=workspace,
    )

    result = workflow.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ", headphones_folder)

    assert result.ok is False
    assert result.outcomes[0].status is TransferStatus.ERROR
    assert "disconnected" in result.outcomes[0].message
    pending = workflow.pending_transfers()[0]
    assert (workspace / "recovery" / pending.filename).read_bytes() == b"verified mp3 payload"
    assert list(headphones_folder.iterdir()) == []


def test_retry_pending_requires_the_verified_remembered_headphones_device(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workspace = artifact_dir / "workspace"
    settings = JsonSettingsStore(artifact_dir / "settings.json")
    correct_identity = HeadphonesDeviceIdentity(kind="test", stable_id="correct-device")
    wrong_identity = HeadphonesDeviceIdentity(kind="test", stable_id="wrong-device")
    first = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        filesystem=FailingFilesystem(),
        settings=settings,
        device_identity=FakeDeviceIdentity(correct_identity),
        workspace=workspace,
    )
    assert first.choose_headphones_folder(headphones_folder).ok is True
    first.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    retry_filesystem = RecordingFilesystem()
    restarted = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        filesystem=retry_filesystem,
        settings=settings,
        device_identity=FakeDeviceIdentity(wrong_identity),
        workspace=workspace,
    )

    result = restarted.retry_pending("yt:test-video")

    assert result.ok is False
    assert result.outcomes[0].status is TransferStatus.ERROR
    assert "identity could not be verified" in result.outcomes[0].message
    assert retry_filesystem.transfers == []
    assert len(restarted.pending_transfers()) == 1


def test_retry_pending_damaged_destination_requires_confirmation_before_repair(
    artifact_dir: Path,
) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workspace = artifact_dir / "workspace"
    workflow = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        filesystem=FailingFilesystem(),
        workspace=workspace,
    )
    workflow.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ", headphones_folder)
    pending = workflow.pending_transfers()[0]
    damaged_destination = headphones_folder / pending.filename
    damaged_destination.write_bytes(b"damaged app-owned copy")
    retry_filesystem = RecordingFilesystem()
    restarted = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        filesystem=retry_filesystem,
        workspace=workspace,
    )

    blocked = restarted.retry_pending("yt:test-video", headphones_folder)

    assert blocked.ok is False
    assert blocked.outcomes[0].status is TransferStatus.REPAIR_NEEDED
    assert "requires confirmation" in blocked.outcomes[0].message
    assert damaged_destination.read_bytes() == b"damaged app-owned copy"
    assert retry_filesystem.transfers == []

    repaired = restarted.retry_pending("yt:test-video", headphones_folder, confirm_repair=True)

    assert repaired.ok is True
    assert damaged_destination.read_bytes() == b"verified mp3 payload"


class ManualCancelToken:
    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


class CancellingExtractor(RecordingExtractor):
    def __init__(self, source_media: Path, cancel_token: ManualCancelToken) -> None:
        super().__init__(source_media)
        self.cancel_token = cancel_token

    def extract(self, url: str, work_dir: Path, cancel_token=None):
        self.calls.append(url)
        self.cancel_token.cancel()
        raise OperationCancelled("Extraction cancelled.")

    def _item_for(self, url: str) -> SourceItem:
        return SourceItem(source_id=f"source:{url}", url=url, title="Cancelling Track")


class CancellingFilesystem(RecordingFilesystem):
    def transfer_verified(self, source: Path, headphones_folder: Path, filename: str, cancel_token=None) -> Path:
        incomplete = headphones_folder / f"{filename}.incomplete"
        incomplete.write_bytes(b"partial")
        if cancel_token is not None:
            cancel_token.cancel()
        raise OperationCancelled("Transfer cancelled.")


def test_run_refuses_selection_when_known_estimate_exceeds_known_free_space(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    extractor = RecordingExtractor(source_media, durations={url: 120.0})
    workflow = Workflow(
        extractor=extractor,
        encoder=WritingEncoder(),
        filesystem=RecordingFilesystem(free_bytes=1_000),
        workspace=artifact_dir / "workspace",
    )

    result = workflow.run([url], headphones_folder)

    assert result.ok is False
    assert result.outcomes[0].status is TransferStatus.ERROR
    assert "estimated to require" in result.outcomes[0].message
    assert extractor.calls == []


def test_selection_space_summary_reports_uncertain_duration_without_refusing(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workflow = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        filesystem=RecordingFilesystem(free_bytes=100_000_000),
        workspace=artifact_dir / "workspace",
    )

    summary = workflow.selection_space_summary(
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        headphones_folder,
    )

    assert summary.item_count == 1
    assert summary.estimated_bytes is None
    assert summary.free_bytes == 100_000_000
    assert summary.unknown_size_count == 1
    assert summary.has_uncertainty is True


def test_cancel_before_run_stops_before_extraction(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    extractor = RecordingExtractor(source_media)
    workflow = Workflow(
        extractor=extractor,
        encoder=WritingEncoder(),
        filesystem=RecordingFilesystem(),
        workspace=artifact_dir / "workspace",
    )

    result = workflow.run(
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        headphones_folder,
        cancel_token=ManualCancelToken(cancelled=True),
    )

    assert result.outcomes[0].status is TransferStatus.CANCELLED
    assert extractor.calls == []
    assert list(headphones_folder.iterdir()) == []


def test_cancel_during_extraction_stops_future_items_and_preserves_completed_transfers(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    token = ManualCancelToken()
    first_url = "https://www.youtube.com/watch?v=first"
    second_url = "https://www.youtube.com/watch?v=second"
    workflow = Workflow(
        extractor=RecordingExtractor(source_media, durations={first_url: 1.0}),
        encoder=WritingEncoder(),
        filesystem=RecordingFilesystem(),
        workspace=artifact_dir / "workspace",
    )

    first = workflow.run([first_url], headphones_folder, cancel_token=token)
    workflow.extractor = CancellingExtractor(source_media, token)
    second = workflow.run([second_url, "https://www.youtube.com/watch?v=third"], headphones_folder, cancel_token=token)

    assert first.outcomes[0].status is TransferStatus.TRANSFERRED
    assert len(second.outcomes) == 1
    assert second.outcomes[0].status is TransferStatus.CANCELLED
    assert len(list(headphones_folder.glob("*.mp3"))) == 1


def test_cancel_during_copy_leaves_no_complete_looking_partial_file(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    workflow = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        filesystem=CancellingFilesystem(),
        workspace=artifact_dir / "workspace",
    )

    result = workflow.run(
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        headphones_folder,
        cancel_token=ManualCancelToken(),
    )

    assert result.outcomes[0].status is TransferStatus.CANCELLED
    pending = workflow.pending_transfers()[0]
    assert (artifact_dir / "workspace" / "recovery" / pending.filename).exists()
    assert list(headphones_folder.iterdir()) == []

def test_keep_local_defaults_off_and_unplugged_transfer_blocks_before_extraction(
    artifact_dir: Path,
) -> None:
    extractor = RecordingExtractor(artifact_dir / "unused.webm")
    workflow = Workflow(
        extractor=extractor,
        encoder=WritingEncoder(),
        settings=JsonSettingsStore(artifact_dir / "settings.json"),
        workspace=artifact_dir / "workspace",
    )

    assert workflow.local_library_settings().enabled is False

    result = workflow.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result.ok is False
    assert extractor.calls == []
    assert result.outcomes[0].status is TransferStatus.ERROR
    assert "Headphones Folder" in result.outcomes[0].message


def test_enabling_keep_local_requires_separate_existing_local_library_and_persists(
    artifact_dir: Path,
) -> None:
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    local_library = artifact_dir / "local-library"
    local_library.mkdir()
    settings = JsonSettingsStore(artifact_dir / "settings.json")
    workflow = Workflow(settings=settings, workspace=artifact_dir / "workspace")

    missing = workflow.configure_local_library(True, artifact_dir / "missing-library")
    assert missing.ok is False
    assert "Local Library" in missing.outcomes[0].message

    same_as_headphones = workflow.configure_local_library(
        True,
        headphones_folder,
        headphones_folder=headphones_folder,
    )
    assert same_as_headphones.ok is False
    assert "separate" in same_as_headphones.outcomes[0].message

    enabled = workflow.configure_local_library(True, local_library, headphones_folder=headphones_folder)
    assert enabled.ok is True

    reloaded = Workflow(settings=settings, workspace=artifact_dir / "workspace").local_library_settings()
    assert reloaded.enabled is True
    assert reloaded.path == local_library


def test_keep_local_downloads_while_unplugged_and_transfers_later_without_redownloading(
    artifact_dir: Path,
) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    local_library = artifact_dir / "local-library"
    local_library.mkdir()
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    settings = JsonSettingsStore(artifact_dir / "settings.json")
    state = JsonTransferStateStore(artifact_dir / "state" / "transfers.json")
    extractor = RecordingExtractor(source_media)
    workflow = Workflow(
        extractor=extractor,
        encoder=WritingEncoder(),
        filesystem=RecordingFilesystem(),
        settings=settings,
        workspace=artifact_dir / "workspace",
        state_store=state,
    )
    assert workflow.configure_local_library(True, local_library).ok is True

    downloaded = workflow.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert downloaded.ok is False
    assert downloaded.outcomes[0].status is TransferStatus.DOWNLOADED
    assert downloaded.outcomes[0].audio_path == local_library / "Lane Mix--a63668cb.mp3"
    assert downloaded.outcomes[0].audio_path.read_bytes() == b"verified mp3 payload"
    assert list(headphones_folder.iterdir()) == []
    local_files = workflow.local_audio_files()
    assert [audio.source_id for audio in local_files] == ["yt:test-video"]
    assert local_files[0].path == local_library / "Lane Mix--a63668cb.mp3"

    retry_extractor = RecordingExtractor(source_media)
    retry_filesystem = RecordingFilesystem()
    restarted = Workflow(
        extractor=retry_extractor,
        encoder=WritingEncoder(),
        filesystem=retry_filesystem,
        settings=settings,
        workspace=artifact_dir / "workspace-2",
        state_store=state,
    )

    transferred = restarted.transfer_local_audio("yt:test-video", headphones_folder)

    assert transferred.ok is True
    assert transferred.outcomes[0].status is TransferStatus.TRANSFERRED
    assert transferred.outcomes[0].transfer_path == headphones_folder / "Lane Mix--a63668cb.mp3"
    assert retry_extractor.calls == []
    assert retry_filesystem.transfers == [
        (local_library / "Lane Mix--a63668cb.mp3", headphones_folder, "Lane Mix--a63668cb.mp3")
    ]


def test_enabled_keep_local_reports_missing_library_instead_of_falling_back(
    artifact_dir: Path,
) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    local_library = artifact_dir / "local-library"
    local_library.mkdir()
    settings = JsonSettingsStore(artifact_dir / "settings.json")
    workflow = Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        settings=settings,
        workspace=artifact_dir / "workspace",
    )
    assert workflow.configure_local_library(True, local_library).ok is True
    local_library.rmdir()

    result = workflow.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result.ok is False
    assert result.outcomes[0].status is TransferStatus.ERROR
    assert "Local Library" in result.outcomes[0].message
    assert not (artifact_dir / "workspace" / "recovery").exists()

