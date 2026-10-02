from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from swimtunes.update import SemVer

POINTER_FILE = "current.json"
PREVIOUS_POINTER_FILE = "previous.json"
COMPLETE_MARKER = "INSTALL_COMPLETE"
VERIFICATION_RECORD = "UPDATE_VERIFIED.json"
DEFAULT_EXECUTABLE = Path("SwimTunes") / "SwimTunes.exe"


def choose_launch_target(portable_root: Path) -> Path | None:
    root = portable_root.resolve()
    for pointer_name in (POINTER_FILE, PREVIOUS_POINTER_FILE):
        version = _read_pointer(root / pointer_name)
        if version is None:
            continue
        target = _verified_target(root, version)
        if target is not None:
            return target
    return _latest_verified_target(root)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    target = choose_launch_target(root)
    if target is None:
        print("SwimTunes could not find a verified installed version to launch.", file=sys.stderr)
        return 1
    completed = subprocess.run([str(target), *argv], check=False)
    return completed.returncode


def _read_pointer(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = payload.get("version") if isinstance(payload, dict) else None
    return version if isinstance(version, str) and version else None


def _verified_target(root: Path, version: str) -> Path | None:
    version_dir = root / "versions" / version
    if not (version_dir / COMPLETE_MARKER).is_file():
        return None
    record_path = version_dir / VERIFICATION_RECORD
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or record.get("version") != version:
        return None
    executable = record.get("executable", str(DEFAULT_EXECUTABLE))
    if not isinstance(executable, str) or Path(executable).is_absolute() or ".." in Path(executable).parts:
        return None
    target = version_dir / executable
    return target if target.is_file() else None


def _latest_verified_target(root: Path) -> Path | None:
    versions_dir = root / "versions"
    if not versions_dir.is_dir():
        return None
    candidates: list[tuple[SemVer, Path]] = []
    for child in versions_dir.iterdir():
        if not child.is_dir() or child.name.endswith(".staging"):
            continue
        target = _verified_target(root, child.name)
        if target is None:
            continue
        try:
            parsed = SemVer.parse(child.name)
        except ValueError:
            continue
        candidates.append((parsed, target))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[-1][1]


if __name__ == "__main__":
    raise SystemExit(main())
