from pathlib import Path

from swimtunes.app import Workflow
from swimtunes.models import MediaInfo, SourcePlaylist, SourcePlaylistEntry, TransferStatus


class PreviewingExtractor:
    def __init__(self, source_media: Path) -> None:
        self.source_media = source_media
        self.preview_calls: list[str] = []
        self.extract_calls: list[str] = []

    def preview(self, url: str) -> SourcePlaylist:
        self.preview_calls.append(url)
        return SourcePlaylist(
            url=url,
            title="Morning Set",
            entries=[
                SourcePlaylistEntry(
                    source_id="yt:lane-1",
                    url="https://www.youtube.com/watch?v=lane1",
                    title="Lane One",
                    available=True,
                ),
                SourcePlaylistEntry(
                    source_id="yt:gone",
                    url="https://www.youtube.com/watch?v=gone",
                    title="Deleted video",
                    available=False,
                    error="This video is unavailable",
                ),
                SourcePlaylistEntry(
                    source_id="yt:lane-2",
                    url="https://www.youtube.com/watch?v=lane2",
                    title="Lane Two",
                    available=True,
                ),
            ],
        )

    def extract(self, url: str, work_dir: Path):
        self.extract_calls.append(url)
        from swimtunes.models import SourceItem

        titles = {
            "https://www.youtube.com/watch?v=lane1": "Lane One",
            "https://www.youtube.com/watch?v=lane2": "Lane Two",
        }
        return SourceItem(source_id=f"yt:{url.rsplit('=', 1)[-1]}", url=url, title=titles[url]), self.source_media


class WritingEncoder:
    def encode_mp3(self, source_media: Path, output_path: Path) -> MediaInfo:
        output_path.write_bytes(f"mp3 from {source_media.name}".encode())
        return MediaInfo(codec="mp3", channels=2, bit_rate=192_000, is_cbr=True)

    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo(codec="mp3", channels=2, bit_rate=192_000, is_cbr=True)


class SoundCloudPlaylistExtractor:
    def __init__(self, source_media: Path) -> None:
        self.source_media = source_media
        self.preview_calls: list[str] = []
        self.extract_calls: list[str] = []

    def preview(self, url: str) -> SourcePlaylist:
        self.preview_calls.append(url)
        return SourcePlaylist(
            url=url,
            title="Morning Laps",
            entries=[
                SourcePlaylistEntry(
                    source_id="soundcloud:steady",
                    url="https://soundcloud.com/swimmer/steady-lap",
                    title="Steady Lap",
                ),
                SourcePlaylistEntry(
                    source_id="soundcloud:removed",
                    url="https://soundcloud.com/swimmer/removed-track",
                    title="Removed Track",
                    available=False,
                    error="This track is unavailable",
                ),
                SourcePlaylistEntry(
                    source_id="soundcloud:ripples",
                    url="https://soundcloud.com/swimmer/ripples",
                    title="Ripples",
                ),
                SourcePlaylistEntry(
                    source_id="soundcloud:unselected",
                    url="https://soundcloud.com/swimmer/not-chosen",
                    title="Not Chosen",
                ),
            ],
        )

    def extract(self, url: str, work_dir: Path):
        self.extract_calls.append(url)
        if url == "https://soundcloud.com/swimmer/ripples":
            raise RuntimeError("extraction failed for this Source Item")
        from swimtunes.models import SourceItem

        return SourceItem(source_id="soundcloud:steady", url=url, title="Steady Lap"), self.source_media


def test_explicit_youtube_playlist_previews_entries_without_downloading(artifact_dir: Path) -> None:
    extractor = PreviewingExtractor(artifact_dir / "source.webm")
    workflow = Workflow(extractor=extractor, encoder=WritingEncoder(), workspace=artifact_dir / "workspace")

    playlist = workflow.preview("https://www.youtube.com/playlist?list=PL123")

    assert playlist.title == "Morning Set"
    assert [entry.title for entry in playlist.entries] == ["Lane One", "Deleted video", "Lane Two"]
    assert [entry.available for entry in playlist.entries] == [True, False, True]
    assert playlist.entries[1].error == "This video is unavailable"
    assert extractor.preview_calls == ["https://www.youtube.com/playlist?list=PL123"]
    assert extractor.extract_calls == []


def test_selected_playlist_entries_alone_are_transferred_with_per_item_outcomes(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    extractor = PreviewingExtractor(source_media)
    workflow = Workflow(extractor=extractor, encoder=WritingEncoder(), workspace=artifact_dir / "workspace")
    playlist = workflow.preview("https://www.youtube.com/playlist?list=PL123")

    result = workflow.transfer_selected([playlist.entries[2], playlist.entries[1]], headphones_folder)

    assert result.ok is False
    assert [outcome.status for outcome in result.outcomes] == [TransferStatus.TRANSFERRED, TransferStatus.ERROR]
    assert result.outcomes[0].title == "Lane Two"
    assert result.outcomes[0].transfer_path == headphones_folder / "Lane Two--5eb3c78c.mp3"
    assert "unavailable" in result.outcomes[1].message
    assert extractor.extract_calls == ["https://www.youtube.com/watch?v=lane2"]
    assert sorted(path.name for path in headphones_folder.iterdir()) == ["Lane Two--5eb3c78c.mp3"]


def test_video_url_with_incidental_list_is_not_a_playlist_preview(artifact_dir: Path) -> None:
    extractor = PreviewingExtractor(artifact_dir / "source.webm")
    workflow = Workflow(extractor=extractor, encoder=WritingEncoder(), workspace=artifact_dir / "workspace")

    playlist = workflow.preview("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123")

    assert playlist.entries == []
    assert "explicit Source Playlist" in (playlist.error or "")
    assert extractor.preview_calls == []


def test_explicit_soundcloud_playlist_previews_entries_without_downloading(artifact_dir: Path) -> None:
    extractor = SoundCloudPlaylistExtractor(artifact_dir / "source.mp3")
    workflow = Workflow(extractor=extractor, encoder=WritingEncoder(), workspace=artifact_dir / "workspace")

    playlist = workflow.preview("https://soundcloud.com/swimmer/sets/morning-laps")

    assert playlist.title == "Morning Laps"
    assert [entry.title for entry in playlist.entries] == [
        "Steady Lap",
        "Removed Track",
        "Ripples",
        "Not Chosen",
    ]
    assert [entry.available for entry in playlist.entries] == [True, False, True, True]
    assert playlist.entries[1].error == "This track is unavailable"
    assert extractor.preview_calls == ["https://soundcloud.com/swimmer/sets/morning-laps"]
    assert extractor.extract_calls == []


def test_soundcloud_track_url_is_not_a_playlist_preview(artifact_dir: Path) -> None:
    extractor = SoundCloudPlaylistExtractor(artifact_dir / "source.mp3")
    workflow = Workflow(extractor=extractor, encoder=WritingEncoder(), workspace=artifact_dir / "workspace")

    playlist = workflow.preview("https://soundcloud.com/swimmer/steady-lap")

    assert playlist.entries == []
    assert "explicit Source Playlist" in (playlist.error or "")
    assert extractor.preview_calls == []


def test_selected_soundcloud_playlist_entries_report_mixed_per_item_outcomes(artifact_dir: Path) -> None:
    source_media = artifact_dir / "source.mp3"
    source_media.write_bytes(b"source media")
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    extractor = SoundCloudPlaylistExtractor(source_media)
    workflow = Workflow(extractor=extractor, encoder=WritingEncoder(), workspace=artifact_dir / "workspace")
    playlist = workflow.preview("https://soundcloud.com/swimmer/sets/morning-laps")

    result = workflow.transfer_selected(playlist.entries[:3], headphones_folder)

    assert result.ok is False
    assert [outcome.status for outcome in result.outcomes] == [
        TransferStatus.TRANSFERRED,
        TransferStatus.ERROR,
        TransferStatus.ERROR,
    ]
    assert result.outcomes[0].title == "Steady Lap"
    assert result.outcomes[0].transfer_path == headphones_folder / "Steady Lap--a403c161.mp3"
    assert "unavailable" in result.outcomes[1].message
    assert "extraction failed" in result.outcomes[2].message
    assert extractor.extract_calls == [
        "https://soundcloud.com/swimmer/steady-lap",
        "https://soundcloud.com/swimmer/ripples",
    ]
    assert sorted(path.name for path in headphones_folder.iterdir()) == ["Steady Lap--a403c161.mp3"]
