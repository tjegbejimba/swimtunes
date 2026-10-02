from __future__ import annotations

from pathlib import Path

from swimtunes.models import SourceItem, SourcePlaylist, SourcePlaylistEntry


class YtDlpExtractor:
    def preview(self, url: str) -> SourcePlaylist:
        import yt_dlp

        options = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
        if info is None:
            raise ValueError("Extractor returned no Source Playlist metadata.")

        entries = []
        for entry in info.get("entries") or []:
            if entry is None:
                continue
            title = str(entry.get("title") or entry.get("id") or "Source Item")
            entry_url = self._entry_url(entry)
            source_id = f"{entry.get('extractor_key') or info.get('extractor_key') or 'source'}:{entry.get('id') or entry_url}"
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

    def extract(self, url: str, work_dir: Path) -> tuple[SourceItem, Path]:
        import yt_dlp

        work_dir.mkdir(parents=True, exist_ok=True)
        options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "outtmpl": str(work_dir / "%(extractor)s-%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
            if info is None:
                raise ValueError("Extractor returned no Source Item metadata.")
            media_path = self._downloaded_path(info, downloader)

        source_id = f"{info.get('extractor_key') or info.get('extractor') or 'source'}:{info.get('id') or url}"
        title = str(info.get("title") or info.get("id") or "Audio File")
        return SourceItem(source_id=source_id, url=url, title=title), media_path

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

    def _downloaded_path(self, info: dict, downloader) -> Path:
        requested = info.get("requested_downloads") or []
        for download in requested:
            filepath = download.get("filepath")
            if filepath:
                return Path(filepath)
        return Path(downloader.prepare_filename(info))
