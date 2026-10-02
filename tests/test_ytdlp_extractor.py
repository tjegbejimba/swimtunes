import json
from pathlib import Path
from types import SimpleNamespace

import swimtunes.adapters.ytdlp as ytdlp_module
from swimtunes.adapters.ytdlp import YtDlpExtractor


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
