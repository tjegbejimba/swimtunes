import subprocess
from pathlib import Path

from swimtunes.adapters.ffmpeg import FfmpegMediaAdapter


def test_ffmpeg_encoder_creates_stereo_192kbps_mp3_fixture(artifact_dir: Path) -> None:
    source_wav = artifact_dir / "tone.wav"
    output_mp3 = artifact_dir / "tone.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-ac",
            "1",
            str(source_wav),
        ],
        check=True,
    )

    media = FfmpegMediaAdapter()
    encoded_info = media.encode_mp3(source_wav, output_mp3)
    probed_info = media.probe(output_mp3)

    assert output_mp3.exists()
    assert encoded_info.is_cbr is True
    assert probed_info.is_cbr is True
    assert encoded_info.is_stereo_192kbps_mp3
    assert probed_info.is_stereo_192kbps_mp3


def test_probe_rejects_vbr_mp3_artifact(artifact_dir: Path) -> None:
    source_wav = artifact_dir / "tone.wav"
    vbr_mp3 = artifact_dir / "tone-vbr.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-ac",
            "2",
            str(source_wav),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_wav),
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(vbr_mp3),
        ],
        check=True,
    )

    probed_info = FfmpegMediaAdapter().probe(vbr_mp3)

    assert probed_info.is_cbr is False
    assert probed_info.is_stereo_192kbps_mp3 is False


def test_probe_rejects_later_bitrate_change_after_first_200_frames(artifact_dir: Path) -> None:
    first_wav = artifact_dir / "first.wav"
    second_wav = artifact_dir / "second.wav"
    first_mp3 = artifact_dir / "first-192.mp3"
    second_mp3 = artifact_dir / "second-128.mp3"
    mixed_mp3 = artifact_dir / "mixed-after-200-frames.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=7",
            "-ac",
            "2",
            str(first_wav),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=1",
            "-ac",
            "2",
            str(second_wav),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(first_wav),
            "-vn",
            "-ac",
            "2",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "192k",
            "-write_xing",
            "0",
            str(first_mp3),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(second_wav),
            "-vn",
            "-ac",
            "2",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-write_xing",
            "0",
            str(second_mp3),
        ],
        check=True,
    )
    mixed_mp3.write_bytes(first_mp3.read_bytes() + second_mp3.read_bytes())

    probed_info = FfmpegMediaAdapter().probe(mixed_mp3)

    assert probed_info.is_cbr is False
    assert probed_info.is_stereo_192kbps_mp3 is False
