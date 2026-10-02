from __future__ import annotations

from pathlib import Path

from swimtunes.models import SourceItem


class YtDlpExtractor:
    def resolve(self, url: str) -> SourceItem:
        import yt_dlp

        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=False)
            if info is None:
                raise ValueError("Extractor returned no Source Item metadata.")
        return self._source_item(info, url)

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

        return self._source_item(info, url), media_path

    def _source_item(self, info: dict, url: str) -> SourceItem:
        source_id = f"{info.get('extractor_key') or info.get('extractor') or 'source'}:{info.get('id') or url}"
        title = str(info.get("title") or info.get("id") or "Audio File")
        return SourceItem(source_id=source_id, url=url, title=title)

    def _downloaded_path(self, info: dict, downloader) -> Path:
        requested = info.get("requested_downloads") or []
        for download in requested:
            filepath = download.get("filepath")
            if filepath:
                return Path(filepath)
        return Path(downloader.prepare_filename(info))
