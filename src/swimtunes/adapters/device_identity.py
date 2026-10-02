from __future__ import annotations

import os
import plistlib
import string
import subprocess
import sys
from pathlib import Path, PureWindowsPath

from swimtunes.models import HeadphonesDeviceIdentity


class UnavailableDeviceIdentityAdapter:
    def identity_for(self, folder: Path) -> HeadphonesDeviceIdentity | None:
        return None

    def find_folder(
        self,
        expected_identity: HeadphonesDeviceIdentity,
        remembered_folder: Path,
    ) -> Path | None:
        return None


class MacOSDeviceIdentityAdapter:
    def identity_for(self, folder: Path) -> HeadphonesDeviceIdentity | None:
        if not folder.exists():
            return None
        try:
            completed = subprocess.run(
                ["diskutil", "info", "-plist", str(folder)],
                check=True,
                capture_output=True,
            )
            data = plistlib.loads(completed.stdout)
        except Exception:
            return None
        stable_id = data.get("VolumeUUID") or data.get("MediaUUID")
        if not stable_id:
            return None
        return HeadphonesDeviceIdentity(kind="macos-volume-uuid", stable_id=str(stable_id))

    def find_folder(
        self,
        expected_identity: HeadphonesDeviceIdentity,
        remembered_folder: Path,
    ) -> Path | None:
        relative = self._relative_to_mount(remembered_folder)
        if relative is None:
            return None
        for mount in [Path("/")] + sorted(Path("/Volumes").glob("*")):
            candidate = mount / relative
            if candidate.exists() and self.identity_for(candidate) == expected_identity:
                return candidate
        return None

    def _relative_to_mount(self, folder: Path) -> Path | None:
        parts = folder.parts
        if len(parts) >= 4 and parts[1] == "Volumes":
            return Path(*parts[3:])
        return folder.relative_to("/") if folder.is_absolute() else folder


class WindowsDeviceIdentityAdapter:
    def identity_for(self, folder: Path) -> HeadphonesDeviceIdentity | None:
        if not folder.exists():
            return None
        drive = PureWindowsPath(str(folder)).drive
        if not drive:
            return None
        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "$v = Get-Volume -DriveLetter "
                        f"{drive.rstrip(':')} -ErrorAction Stop; "
                        "if ($v.UniqueId) { $v.UniqueId } elseif ($v.SerialNumber) { $v.SerialNumber }"
                    ),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            return None
        stable_id = completed.stdout.strip()
        if not stable_id:
            return None
        return HeadphonesDeviceIdentity(kind="windows-volume-serial", stable_id=stable_id)

    def find_folder(
        self,
        expected_identity: HeadphonesDeviceIdentity,
        remembered_folder: Path,
    ) -> Path | None:
        remembered = PureWindowsPath(str(remembered_folder))
        relative = remembered.relative_to(remembered.anchor) if remembered.anchor else remembered
        for letter in string.ascii_uppercase:
            candidate = Path(f"{letter}:\\") / Path(*relative.parts)
            if candidate.exists() and self.identity_for(candidate) == expected_identity:
                return candidate
        return None


def platform_device_identity_adapter():
    if sys.platform == "darwin":
        return MacOSDeviceIdentityAdapter()
    if os.name == "nt":
        return WindowsDeviceIdentityAdapter()
    return UnavailableDeviceIdentityAdapter()
