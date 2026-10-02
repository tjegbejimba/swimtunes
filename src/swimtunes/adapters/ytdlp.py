from __future__ import annotations

import json
import subprocess
from pathlib import Path

from swimtunes.models import SourceItem
from swimtunes.tools import bundled_root, resolve_required_tools


class YtDlpExtractor:
    def __init__(self, yt_dlp: str | Path | None = None) -> None:
        if yt_dlp is None:
            yt_dlp = resolve_required_tools(bundle_root=bundled_root()).yt_dlp
        self.yt_dlp = str(yt_dlp)

    def extract(self, url: str, work_dir: Path) -> tuple[SourceItem, Path]:
        work_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                self.yt_dlp,
                "--dump-single-json",
                "--no-simulate",
                "--format",
                "bestaudio/best",
                "--no-playlist",
                "--no-warnings",
                "--quiet",
                "--paths",
                str(work_dir),
                "--output",
                "%(extractor)s-%(id)s.%(ext)s",
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        info = json.loads(completed.stdout)
        media_path = self._downloaded_path(info, work_dir)
        source_id = f"{info.get('extractor_key') or info.get('extractor') or 'source'}:{info.get('id') or url}"
        title = str(info.get("title") or info.get("id") or "Audio File")
        return SourceItem(source_id=source_id, url=url, title=title), media_path

    def _downloaded_path(self, info: dict, work_dir: Path) -> Path:
        requested = info.get("requested_downloads") or []
        for download in requested:
            filepath = download.get("filepath")
            if filepath:
                return Path(filepath)
        filename = f"{info.get('extractor') or 'source'}-{info.get('id')}.{info.get('ext')}"
        return work_dir / filename
