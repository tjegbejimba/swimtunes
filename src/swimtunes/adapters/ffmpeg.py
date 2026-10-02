from __future__ import annotations

import json
import subprocess
from pathlib import Path

from swimtunes.models import MediaInfo
from swimtunes.tools import bundled_root, resolve_tool


_BITRATES_KBPS = {
    ("mpeg1", "layer3"): [None, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, None],
    ("mpeg2", "layer3"): [None, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, None],
    ("mpeg2.5", "layer3"): [None, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, None],
}
_SAMPLE_RATES = {
    "mpeg1": [44100, 48000, 32000, None],
    "mpeg2": [22050, 24000, 16000, None],
    "mpeg2.5": [11025, 12000, 8000, None],
}


class FfmpegMediaAdapter:
    def __init__(self, ffmpeg: str | Path | None = None, ffprobe: str | Path | None = None) -> None:
        self.ffmpeg = str(ffmpeg) if ffmpeg is not None else None
        self.ffprobe = str(ffprobe) if ffprobe is not None else None

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
            is_cbr=self._is_constant_bitrate_mp3(path),
        )

    def _is_constant_bitrate_mp3(self, path: Path) -> bool:
        data = path.read_bytes()
        offset = self._skip_id3v2(data)
        bitrates: list[int] = []
        channels_seen: list[int] = []
        first_frame_start: int | None = None
        first_frame_channels: int | None = None
        first_frame_version: str | None = None

        while offset + 4 <= len(data):
            header = int.from_bytes(data[offset : offset + 4], "big")
            parsed = self._parse_mp3_frame_header(header)
            if parsed is None:
                offset += 1
                continue
            version, bitrate, frame_size, channels = parsed
            if first_frame_start is None:
                first_frame_start = offset
                first_frame_channels = channels
                first_frame_version = version
            bitrates.append(bitrate)
            channels_seen.append(channels)
            offset += frame_size

        if not bitrates or first_frame_start is None:
            return False
        if self._has_vbr_header(data, first_frame_start, first_frame_version, first_frame_channels):
            return False
        return len(set(bitrates)) == 1 and bitrates[0] == 192_000 and set(channels_seen) == {2}

    def _skip_id3v2(self, data: bytes) -> int:
        if len(data) < 10 or data[:3] != b"ID3":
            return 0
        size = (
            (data[6] << 21)
            | (data[7] << 14)
            | (data[8] << 7)
            | data[9]
        )
        return 10 + size

    def _parse_mp3_frame_header(self, header: int) -> tuple[str, int, int, int] | None:
        if (header & 0xFFE00000) != 0xFFE00000:
            return None

        version_bits = (header >> 19) & 0b11
        layer_bits = (header >> 17) & 0b11
        bitrate_index = (header >> 12) & 0b1111
        sample_rate_index = (header >> 10) & 0b11
        padding = (header >> 9) & 0b1
        channel_mode = (header >> 6) & 0b11

        version = {0b11: "mpeg1", 0b10: "mpeg2", 0b00: "mpeg2.5"}.get(version_bits)
        layer = {0b01: "layer3"}.get(layer_bits)
        if version is None or layer is None:
            return None

        bitrate_table = _BITRATES_KBPS[(version, layer)]
        sample_rate_table = _SAMPLE_RATES[version]
        bitrate_kbps = bitrate_table[bitrate_index]
        sample_rate = sample_rate_table[sample_rate_index]
        if bitrate_kbps is None or sample_rate is None:
            return None

        bitrate = bitrate_kbps * 1000
        if version == "mpeg1":
            frame_size = int((144 * bitrate) / sample_rate) + padding
        else:
            frame_size = int((72 * bitrate) / sample_rate) + padding
        channels = 1 if channel_mode == 0b11 else 2
        return version, bitrate, frame_size, channels

    def _has_vbr_header(
        self,
        data: bytes,
        frame_start: int,
        version: str | None,
        channels: int | None,
    ) -> bool:
        if version is None or channels is None:
            return True
        xing_offset = 21 if version == "mpeg1" and channels == 1 else 36
        if version != "mpeg1":
            xing_offset = 13 if channels == 1 else 21
        xing_marker = data[frame_start + xing_offset : frame_start + xing_offset + 4]
        vbri_marker = data[frame_start + 36 : frame_start + 40]
        return xing_marker in {b"Xing", b"Info"} or vbri_marker == b"VBRI"

    @property
    def ffmpeg(self) -> str:
        if self._ffmpeg is None:
            self._ffmpeg = str(resolve_tool("ffmpeg", bundle_root=bundled_root()))
        return self._ffmpeg

    @ffmpeg.setter
    def ffmpeg(self, value: str | None) -> None:
        self._ffmpeg = value

    @property
    def ffprobe(self) -> str:
        if self._ffprobe is None:
            self._ffprobe = str(resolve_tool("ffprobe", bundle_root=bundled_root()))
        return self._ffprobe

    @ffprobe.setter
    def ffprobe(self, value: str | None) -> None:
        self._ffprobe = value
