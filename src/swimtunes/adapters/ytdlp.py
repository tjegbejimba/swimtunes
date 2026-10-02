from __future__ import annotations

import json
import subprocess
from pathlib import Path

from swimtunes.models import SourceItem, SourcePlaylist, SourcePlaylistEntry
from swimtunes.tools import bundled_root, resolve_tool


class YtDlpExtractor:
    def __init__(self, yt_dlp: str | Path | None = None) -> None:
        self.yt_dlp = str(yt_dlp) if yt_dlp is not None else None

    def preview(self, url: str) -> SourcePlaylist:
        info = self._run_json(
            [
                "--flat-playlist",
                "-J",
                "--no-warnings",
                "--quiet",
                url,
            ]
        )
        entries = []
        for entry in info.get("entries") or []:
            if entry is None:
                continue
            title = str(entry.get("title") or entry.get("id") or "Source Item")
            entry_url = self._entry_url(entry)
            source_id = (
                f"{entry.get('extractor_key') or info.get('extractor_key') or 'source'}:"
                f"{entry.get('id') or entry_url}"
            )
            error = self._entry_error(entry)
            entries.append(
                SourcePlaylistEntry(
                    source_id=source_id,
                    url=entry_url,
                    title=title,
                    available=error is None,
                    error=error,
                )
            )
        return SourcePlaylist(
            url=url,
            title=str(info.get("title") or "Source Playlist"),
            entries=entries,
        )

    def resolve(self, url: str) -> SourceItem:
        info = self._run_json(
            [
                "--skip-download",
                "-J",
                "--no-playlist",
                "--no-warnings",
                "--quiet",
                url,
            ]
        )
        return self._source_item(info, url)

    def extract(self, url: str, work_dir: Path) -> tuple[SourceItem, Path]:
        work_dir.mkdir(parents=True, exist_ok=True)
        info = self._run_json(
            [
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
            ]
        )
        media_path = self._downloaded_path(info, work_dir)
        return self._source_item(info, url), media_path

    def _run_json(self, args: list[str]) -> dict:
        completed = subprocess.run(
            [self.yt_dlp, *args],
            check=True,
            capture_output=True,
            text=True,
        )
        if not completed.stdout.strip():
            raise ValueError("Extractor returned no Source metadata.")
        return json.loads(completed.stdout)

    def _source_item(self, info: dict, url: str) -> SourceItem:
        source_id = f"{info.get('extractor_key') or info.get('extractor') or 'source'}:{info.get('id') or url}"
        title = str(info.get("title") or info.get("id") or "Audio File")
        return SourceItem(source_id=source_id, url=url, title=title)

    def _entry_url(self, entry: dict) -> str:
        webpage_url = entry.get("webpage_url")
        if webpage_url:
            return str(webpage_url)
        url = str(entry.get("url") or "")
        if url.startswith(("http://", "https://")):
            return url
        if entry.get("ie_key") == "Youtube" or entry.get("extractor_key") == "Youtube":
            return f"https://www.youtube.com/watch?v={url or entry.get('id')}"
        return url

    def _entry_error(self, entry: dict) -> str | None:
        title = str(entry.get("title") or "")
        if title in {"[Deleted video]", "[Private video]"}:
            return title.strip("[]")
        availability = entry.get("availability")
        if availability in {"private", "premium_only", "subscriber_only", "needs_auth"}:
            return str(availability).replace("_", " ")
        reason = entry.get("reason") or entry.get("error")
        return str(reason) if reason else None

    def _downloaded_path(self, info: dict, work_dir: Path) -> Path:
        requested = info.get("requested_downloads") or []
        for download in requested:
            filepath = download.get("filepath")
            if filepath:
                return Path(filepath)
        filename = f"{info.get('extractor') or 'source'}-{info.get('id')}.{info.get('ext')}"
        return work_dir / filename

    @property
    def yt_dlp(self) -> str:
        if self._yt_dlp is None:
            self._yt_dlp = str(resolve_tool("yt-dlp", bundle_root=bundled_root()))
        return self._yt_dlp

    @yt_dlp.setter
    def yt_dlp(self, value: str | None) -> None:
        self._yt_dlp = value
