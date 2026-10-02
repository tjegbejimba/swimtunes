from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swimtunes.models import HeadphonesDeviceIdentity, HeadphonesFolderSelection, LocalLibrarySettings
from swimtunes.paths import default_settings_path


class JsonSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_settings_path()

    def load_headphones_folder(self) -> HeadphonesFolderSelection | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text())
        headphones = data.get("headphones_folder")
        if not isinstance(headphones, dict):
            return None
        identity = headphones.get("device_identity")
        path = headphones.get("path")
        if not isinstance(path, str) or not isinstance(identity, dict):
            return None
        kind = identity.get("kind")
        stable_id = identity.get("stable_id")
        if not isinstance(kind, str) or not isinstance(stable_id, str):
            return None
        return HeadphonesFolderSelection(
            path=Path(path),
            device_identity=HeadphonesDeviceIdentity(kind=kind, stable_id=stable_id),
        )

    def save_headphones_folder(self, selection: HeadphonesFolderSelection) -> None:
        data = self._load_raw()
        data["headphones_folder"] = {
            "path": str(selection.path),
            "device_identity": {
                "kind": selection.device_identity.kind,
                "stable_id": selection.device_identity.stable_id,
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    def load_local_library(self) -> LocalLibrarySettings:
        if not self.path.exists():
            return LocalLibrarySettings()
        data = self._load_raw()
        local_library = data.get("local_library")
        if not isinstance(local_library, dict):
            return LocalLibrarySettings()
        enabled = bool(local_library.get("enabled", False))
        path = local_library.get("path")
        if not enabled:
            return LocalLibrarySettings(enabled=False, path=None)
        if not isinstance(path, str):
            return LocalLibrarySettings(enabled=True, path=None)
        return LocalLibrarySettings(enabled=True, path=Path(path))

    def save_local_library(self, settings: LocalLibrarySettings) -> None:
        data = self._load_raw()
        data["local_library"] = {
            "enabled": settings.enabled,
            "path": str(settings.path) if settings.path is not None else None,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    def _load_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text())
        return data if isinstance(data, dict) else {}
