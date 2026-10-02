from __future__ import annotations

import argparse
import zipfile
from pathlib import Path, PurePosixPath


class PayloadAuditError(RuntimeError):
    pass


def audit_payload(payload: Path) -> None:
    if not payload.is_dir():
        raise PayloadAuditError(f"release payload is not a directory: {payload}")
    violations: list[str] = []
    for path in payload.rglob("*"):
        relative = path.relative_to(payload).as_posix()
        if _is_app_source_path(PurePosixPath(relative)):
            violations.append(relative)
        if path.is_file() and path.suffix.lower() == ".zip":
            violations.extend(_audit_zip(path))
    if violations:
        formatted = "\n".join(f"- {violation}" for violation in violations)
        raise PayloadAuditError(f"release payload contains app source files or directories:\n{formatted}")


def _audit_zip(path: Path) -> list[str]:
    violations: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if _is_app_source_path(PurePosixPath(name)):
                    violations.append(f"{path.name}!/{name}")
    except zipfile.BadZipFile:
        return violations
    return violations


def _is_app_source_path(path: PurePosixPath) -> bool:
    parts = path.parts
    if any(part in {"src", "tests"} for part in parts):
        return True
    return path.suffix == ".py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail if a public release payload leaks SwimTunes app source.")
    parser.add_argument("payload", type=Path)
    args = parser.parse_args()
    audit_payload(args.payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
