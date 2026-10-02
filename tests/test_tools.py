from __future__ import annotations

import json
import os
from pathlib import Path

from swimtunes.tools import resolve_required_tools, self_check


def test_bundled_tool_resolution_prefers_frozen_bundle(artifact_dir: Path) -> None:
    bundle_root = artifact_dir / "bundle"
    tools_dir = bundle_root / "tools"
    tools_dir.mkdir(parents=True)
    bundled_ffmpeg = tools_dir / "ffmpeg.exe"
    bundled_ffprobe = tools_dir / "ffprobe.exe"
    bundled_ytdlp = tools_dir / "yt-dlp.exe"
    for path in (bundled_ffmpeg, bundled_ffprobe, bundled_ytdlp):
        path.write_text("placeholder", encoding="utf-8")

    resolved = resolve_required_tools(bundle_root=bundle_root, path_env="", platform="win32")

    assert resolved.ffmpeg == bundled_ffmpeg
    assert resolved.ffprobe == bundled_ffprobe
    assert resolved.yt_dlp == bundled_ytdlp


def test_dev_tool_resolution_falls_back_to_path(artifact_dir: Path) -> None:
    bin_dir = artifact_dir / "bin"
    bin_dir.mkdir()
    suffix = ".exe" if os.name == "nt" else ""
    platform = "win32" if os.name == "nt" else "linux"
    ffmpeg = bin_dir / f"ffmpeg{suffix}"
    ffprobe = bin_dir / f"ffprobe{suffix}"
    ytdlp = bin_dir / f"yt-dlp{suffix}"
    for path in (ffmpeg, ffprobe, ytdlp):
        path.write_text("placeholder", encoding="utf-8")
        path.chmod(0o755)

    resolved = resolve_required_tools(bundle_root=None, path_env=str(bin_dir), platform=platform)

    assert resolved.ffmpeg == ffmpeg
    assert resolved.ffprobe == ffprobe
    assert resolved.yt_dlp == ytdlp


def test_self_check_uses_manifest_and_reports_versions(artifact_dir: Path) -> None:
    bundle_root = artifact_dir / "bundle"
    tools_dir = bundle_root / "tools"
    tools_dir.mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe", "yt-dlp.exe"):
        (tools_dir / name).write_text("placeholder", encoding="utf-8")
    manifest = {
        "tools": {
            "ffmpeg": {"version": "9.0.2", "origin": "https://example.test/ffmpeg.zip"},
            "yt-dlp": {"version": "2026.08.19", "origin": "https://example.test/yt-dlp.exe"},
        }
    }
    (tools_dir / "TOOL_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    seen: list[Path] = []

    def fake_runner(path: Path, args: list[str]) -> str:
        seen.append(path)
        return f"{path.name} version line"

    report = self_check(
        bundle_root=bundle_root,
        path_env="",
        platform="win32",
        run_tool=fake_runner,
        require_manifest=True,
    )

    assert report.ok is True
    assert report.manifest == manifest
    assert report.versions["ffmpeg"] == "ffmpeg.exe version line"
    assert report.versions["yt-dlp"] == "yt-dlp.exe version line"
    assert seen == [tools_dir / "ffmpeg.exe", tools_dir / "ffprobe.exe", tools_dir / "yt-dlp.exe"]
