import sys
from pathlib import Path
from types import SimpleNamespace

from swimtunes.adapters.ytdlp import YtDlpExtractor


def test_youtube_video_with_incidental_list_is_extracted_as_one_source_item(
    artifact_dir: Path,
    monkeypatch,
) -> None:
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def extract_info(self, url, download):
            captured["url"] = url
            captured["download"] = download
            media = artifact_dir / "downloaded.webm"
            media.write_bytes(b"source media")
            return {
                "extractor_key": "Youtube",
                "id": "abc123",
                "title": "Lane Mix",
                "requested_downloads": [{"filepath": str(media)}],
            }

        def prepare_filename(self, info):
            raise AssertionError("requested download filepath should be used")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    source_item, media_path = YtDlpExtractor().extract(
        "https://www.youtube.com/watch?v=abc123&list=PLincidental",
        artifact_dir / "extract",
    )

    assert captured["options"]["noplaylist"] is True
    assert captured["url"] == "https://www.youtube.com/watch?v=abc123&list=PLincidental"
    assert captured["download"] is True
    assert source_item.source_id == "Youtube:abc123"
    assert source_item.title == "Lane Mix"
    assert media_path == artifact_dir / "downloaded.webm"
