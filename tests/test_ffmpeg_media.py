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
    assert encoded_info.is_stereo_192kbps_mp3
    assert probed_info.is_stereo_192kbps_mp3
