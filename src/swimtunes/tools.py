from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


RunTool = Callable[[Path, list[str]], str]


@dataclass(frozen=True)
class ToolPaths:
    ffmpeg: Path
    ffprobe: Path
    yt_dlp: Path


@dataclass(frozen=True)
class SelfCheckReport:
    ok: bool
    tools: ToolPaths | None
    versions: dict[str, str]
    manifest: Mapping[str, object] | None
    message: str


def bundled_root() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(sys.executable).resolve().parent


def resolve_required_tools(
    *,
    bundle_root: Path | None = None,
    path_env: str | None = None,
    platform: str | None = None,
) -> ToolPaths:
    platform = platform or sys.platform
    return ToolPaths(
        ffmpeg=resolve_tool("ffmpeg", bundle_root=bundle_root, path_env=path_env, platform=platform),
        ffprobe=resolve_tool("ffprobe", bundle_root=bundle_root, path_env=path_env, platform=platform),
        yt_dlp=resolve_tool("yt-dlp", bundle_root=bundle_root, path_env=path_env, platform=platform),
    )


def resolve_tool(
    name: str,
    *,
    bundle_root: Path | None = None,
    path_env: str | None = None,
    platform: str | None = None,
) -> Path:
    return _resolve_tool(name, bundle_root, path_env, platform or sys.platform)


def self_check(
    *,
    bundle_root: Path | None = None,
    path_env: str | None = None,
    platform: str | None = None,
    run_tool: RunTool | None = None,
    require_manifest: bool | None = None,
) -> SelfCheckReport:
    bundle_root = bundled_root() if bundle_root is None else bundle_root
    require_manifest = bundle_root is not None if require_manifest is None else require_manifest
    try:
        tools = resolve_required_tools(
            bundle_root=bundle_root,
            path_env=path_env,
            platform=platform,
        )
        manifest = _read_manifest(bundle_root)
        if require_manifest and manifest is None:
            raise FileNotFoundError("Bundled tool manifest was not found.")
        runner = run_tool or _run_version
        versions = {
            "ffmpeg": runner(tools.ffmpeg, ["-version"]).splitlines()[0],
            "ffprobe": runner(tools.ffprobe, ["-version"]).splitlines()[0],
            "yt-dlp": runner(tools.yt_dlp, ["--version"]).splitlines()[0],
        }
    except Exception as exc:
        return SelfCheckReport(
            ok=False,
            tools=None,
            versions={},
            manifest=None,
            message=str(exc),
        )
    return SelfCheckReport(
        ok=True,
        tools=tools,
        versions=versions,
        manifest=manifest,
        message="Self-check passed.",
    )


def _resolve_tool(
    name: str,
    bundle_root: Path | None,
    path_env: str | None,
    platform: str,
) -> Path:
    filename = _tool_filename(name, platform)
    for candidate in _bundled_candidates(filename, bundle_root):
        if candidate.is_file():
            return candidate

    resolved = shutil.which(filename, path=path_env)
    if resolved is None and filename != name:
        resolved = shutil.which(name, path=path_env)
    if resolved is None:
        raise FileNotFoundError(f"Required tool {filename} was not found in the bundle or PATH.")
    return Path(resolved)


def _tool_filename(name: str, platform: str) -> str:
    if platform.startswith("win") and not name.endswith(".exe"):
        return f"{name}.exe"
    return name


def _bundled_candidates(filename: str, bundle_root: Path | None) -> list[Path]:
    if bundle_root is None:
        return []
    return [
        bundle_root / "tools" / filename,
        bundle_root / "_internal" / "tools" / filename,
        bundle_root.parent / "tools" / filename,
    ]


def _read_manifest(bundle_root: Path | None) -> Mapping[str, object] | None:
    if bundle_root is None:
        return None
    for candidate in _bundled_candidates("TOOL_MANIFEST.json", bundle_root):
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def _run_version(path: Path, args: list[str]) -> str:
    completed = subprocess.run(
        [str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONNOUSERSITE": "1"},
    )
    return completed.stdout.strip() or completed.stderr.strip()
