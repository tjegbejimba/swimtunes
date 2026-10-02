from pathlib import Path

from swimtunes.app import Workflow
from swimtunes.models import MediaInfo, SourceItem, TransferStatus
from swimtunes.state import JsonTransferStateStore


class RecordingExtractor:
    def __init__(
        self,
        source_media: Path,
        source_item: SourceItem | None = None,
        titles: dict[str, str] | None = None,
    ) -> None:
        self.source_media = source_media
        self.source_item = source_item
        self.titles = titles or {}
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
        return SourceItem(source_id="yt:test-video", url=url, title=title)


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
    assert progress[0].audio_path == artifact_dir / "workspace" / "audio" / "Lane Mix--a63668cb.mp3"
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
    assert result.outcomes[0].status is TransferStatus.ERROR
    assert "repair" in result.outcomes[0].message
    assert extractor.calls == []
