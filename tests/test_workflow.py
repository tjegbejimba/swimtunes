from pathlib import Path

from swimtunes.app import Workflow
from swimtunes.models import MediaInfo, SourceItem, TransferStatus


class RecordingExtractor:
    def __init__(self, source_media: Path) -> None:
        self.source_media = source_media
        self.calls: list[str] = []

    def extract(self, url: str, work_dir: Path):
        self.calls.append(url)
        return SourceItem(source_id="yt:test-video", url=url, title="Lane Mix"), self.source_media


class WritingEncoder:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def encode_mp3(self, source_media: Path, output_path: Path) -> MediaInfo:
        self.calls.append((source_media, output_path))
        output_path.write_bytes(b"verified mp3 payload")
        return MediaInfo(codec="mp3", channels=2, bit_rate=192_000)

    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo(codec="mp3", channels=2, bit_rate=192_000)


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
    assert outcome.transfer_path == headphones_folder / "Lane Mix.mp3"
    assert outcome.transfer_path.read_bytes() == b"verified mp3 payload"
    assert not (headphones_folder / "Lane Mix.mp3.incomplete").exists()


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
