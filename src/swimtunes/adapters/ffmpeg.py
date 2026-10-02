from __future__ import annotations

import json
import subprocess
from pathlib import Path

from swimtunes.models import MediaInfo


class FfmpegMediaAdapter:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def encode_mp3(self, source_media: Path, output_path: Path) -> MediaInfo:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source_media),
                "-vn",
                "-ac",
                "2",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "192k",
                "-write_xing",
                "0",
                str(output_path),
            ],
            check=True,
        )
        return self.probe(output_path)

    def probe(self, path: Path) -> MediaInfo:
        completed = subprocess.run(
            [
                self.ffprobe,
                "-hide_banner",
                "-loglevel",
                "error",
                "-show_entries",
                "stream=codec_name,channels,bit_rate:format=bit_rate",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(completed.stdout)
        audio_stream = next(
            (stream for stream in data.get("streams", []) if stream.get("codec_name")),
            None,
        )
        if audio_stream is None:
            raise ValueError(f"No audio stream found in {path}")
        bit_rate = int(audio_stream.get("bit_rate") or data.get("format", {}).get("bit_rate") or 0)
        return MediaInfo(
            codec=str(audio_stream.get("codec_name", "")),
            channels=int(audio_stream.get("channels") or 0),
            bit_rate=bit_rate,
        )
