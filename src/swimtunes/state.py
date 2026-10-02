from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from swimtunes.models import TransferRecord


class JsonTransferStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, source_id: str) -> TransferRecord | None:
        data = self._read()
        raw = data.get(source_id)
        if not isinstance(raw, dict):
            return None
        return self._record_from(raw)

    def save(self, record: TransferRecord) -> None:
        data = self._read()
        data[record.source_id] = asdict(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def all(self) -> list[TransferRecord]:
        return [
            record
            for raw in self._read().values()
            if isinstance(raw, dict) and (record := self._record_from(raw)) is not None
        ]

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Transfer state file is not a JSON object.")
        return raw

    def _record_from(self, raw: dict[str, Any]) -> TransferRecord | None:
        try:
            return TransferRecord(
                source_id=str(raw["source_id"]),
                title=str(raw["title"]),
                filename=str(raw["filename"]),
                byte_size=int(raw["byte_size"]),
                sha256=str(raw["sha256"]),
                source_url=str(raw.get("source_url", "")),
                status=str(raw.get("status", "completed")),
            )
        except (KeyError, TypeError, ValueError):
            return None
