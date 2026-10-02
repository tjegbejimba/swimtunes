from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swimtunes.models import HeadphonesDeviceIdentity, HeadphonesFolderSelection
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

    def _load_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text())
        return data if isinstance(data, dict) else {}
