import json
from pathlib import Path
from types import SimpleNamespace

import swimtunes.adapters.ytdlp as ytdlp_module
from swimtunes.adapters.ytdlp import YtDlpExtractor


def test_playlist_preview_uses_flat_bundled_executable_and_preserves_entry_metadata(
    artifact_dir: Path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_run(command, check, capture_output, text):
        captured["command"] = command
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["text"] = text
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "extractor_key": "YoutubeTab",
                    "title": "Morning Laps",
                    "entries": [
                        {
                            "extractor_key": "Youtube",
                            "id": "lane1",
                            "url": "lane1",
                            "title": "Lane One",
                        },
                        {
                            "extractor_key": "Youtube",
                            "id": "gone",
                            "url": "gone",
                            "title": "[Deleted video]",
                        },
                    ],
                }
            )
        )

    monkeypatch.setattr(ytdlp_module.subprocess, "run", fake_run)

    playlist = YtDlpExtractor(yt_dlp=artifact_dir / "yt-dlp").preview(
        "https://www.youtube.com/playlist?list=PL123"
    )

    assert captured["command"] == [
        str(artifact_dir / "yt-dlp"),
        "--flat-playlist",
        "-J",
        "--no-warnings",
        "--quiet",
        "https://www.youtube.com/playlist?list=PL123",
    ]
    assert captured["check"] is True
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert playlist.title == "Morning Laps"
    assert playlist.entries[0].source_id == "Youtube:lane1"
    assert playlist.entries[0].url == "https://www.youtube.com/watch?v=lane1"
    assert playlist.entries[0].available is True
    assert playlist.entries[1].source_id == "Youtube:gone"
    assert playlist.entries[1].available is False
    assert playlist.entries[1].error == "Deleted video"


def test_resolve_uses_metadata_only_no_playlist_command(artifact_dir: Path, monkeypatch) -> None:
    captured = {}

    def fake_run(command, check, capture_output, text):
        captured["command"] = command
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "extractor_key": "Soundcloud",
                    "id": "steady-lap",
                    "title": "Steady Lap",
                }
            )
        )

    monkeypatch.setattr(ytdlp_module.subprocess, "run", fake_run)

    source_item = YtDlpExtractor(yt_dlp=artifact_dir / "yt-dlp").resolve(
        "https://soundcloud.com/swimmer/steady-lap"
    )

    assert captured["command"] == [
        str(artifact_dir / "yt-dlp"),
        "--skip-download",
        "-J",
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        "https://soundcloud.com/swimmer/steady-lap",
    ]
    assert source_item.source_id == "Soundcloud:steady-lap"
    assert source_item.title == "Steady Lap"


def test_youtube_video_with_incidental_list_is_extracted_as_one_source_item(
    artifact_dir: Path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_run(command, check, capture_output, text):
        captured["command"] = command
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["text"] = text
        media = artifact_dir / "downloaded.webm"
        media.write_bytes(b"source media")
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "extractor_key": "Youtube",
                    "extractor": "youtube",
                    "id": "abc123",
                    "title": "Lane Mix",
                    "requested_downloads": [{"filepath": str(media)}],
                }
            )
        )

    monkeypatch.setattr(ytdlp_module.subprocess, "run", fake_run)

    source_item, media_path = YtDlpExtractor(yt_dlp=artifact_dir / "yt-dlp").extract(
        "https://www.youtube.com/watch?v=abc123&list=PLincidental",
        artifact_dir / "extract",
    )

    assert "--no-playlist" in captured["command"]
    assert captured["command"][-1] == "https://www.youtube.com/watch?v=abc123&list=PLincidental"
    assert captured["check"] is True
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert source_item.source_id == "Youtube:abc123"
    assert source_item.title == "Lane Mix"
    assert media_path == artifact_dir / "downloaded.webm"
