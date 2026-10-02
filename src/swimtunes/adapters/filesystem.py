from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path


class LocalFilesystemAdapter:
    def ensure_headphones_folder(self, folder: Path) -> None:
        if not folder.exists() or not folder.is_dir():
            raise ValueError("Choose an existing Headphones Folder before starting.")

    def safe_audio_filename(self, title: str) -> str:
        stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "-", title).strip(" .")
        stem = re.sub(r"\s+", " ", stem)[:120].strip()
        return f"{stem or 'Audio File'}.mp3"

    def transfer_verified(self, source: Path, headphones_folder: Path, filename: str) -> Path:
        self.ensure_headphones_folder(headphones_folder)
        destination = (headphones_folder / filename).resolve()
        root = headphones_folder.resolve()
        if root != destination.parent:
            raise ValueError("Transfer destination must stay inside the Headphones Folder.")

        incomplete = destination.with_name(f"{destination.name}.incomplete")
        source_hash = self._sha256(source)
        source_size = source.stat().st_size

        with source.open("rb") as src, incomplete.open("wb") as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())

        if incomplete.stat().st_size != source_size or self._sha256(incomplete) != source_hash:
            incomplete.unlink(missing_ok=True)
            raise IOError("Transfer verification failed before completion.")

        os.replace(incomplete, destination)
        if destination.stat().st_size != source_size or self._sha256(destination) != source_hash:
            raise IOError("Completed Transfer verification failed.")
        return destination

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
