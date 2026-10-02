from __future__ import annotations

import os
import sys
from pathlib import Path


def default_workspace() -> Path:
    app_name = "SwimTunes"
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / app_name
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        root = Path(base) / app_name
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        root = Path(base) / app_name
    return root / "workspace"
