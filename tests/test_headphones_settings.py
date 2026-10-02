from pathlib import Path

from swimtunes.adapters.settings import JsonSettingsStore
from swimtunes.app import Workflow
from swimtunes.models import HeadphonesDeviceIdentity, MediaInfo, SourceItem, TransferStatus


class RecordingExtractor:
    def __init__(self, source_media: Path) -> None:
        self.source_media = source_media
        self.resolve_calls: list[str] = []
        self.calls: list[str] = []

    def resolve(self, url: str):
        self.resolve_calls.append(url)
        return SourceItem(source_id="yt:test-video", url=url, title="Lane Mix")

    def extract(self, url: str, work_dir: Path):
        self.calls.append(url)
        return SourceItem(source_id="yt:test-video", url=url, title="Lane Mix"), self.source_media


class WritingEncoder:
    def encode_mp3(self, source_media: Path, output_path: Path) -> MediaInfo:
        output_path.write_bytes(b"verified mp3 payload")
        return MediaInfo(codec="mp3", channels=2, bit_rate=192_000, is_cbr=True)

    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo(codec="mp3", channels=2, bit_rate=192_000, is_cbr=True)


class SimulatedDeviceIdentities:
    def __init__(self, identities: dict[Path, HeadphonesDeviceIdentity | None]) -> None:
        self.identities = {path.resolve(): identity for path, identity in identities.items()}

    def identity_for(self, folder: Path) -> HeadphonesDeviceIdentity | None:
        return self.identities.get(folder.resolve())

    def find_folder(
        self,
        expected_identity: HeadphonesDeviceIdentity,
        remembered_folder: Path,
    ) -> Path | None:
        remembered_name = remembered_folder.name
        for folder, identity in self.identities.items():
            if identity == expected_identity and folder.name == remembered_name and folder.exists():
                return folder
        return None


def _workflow(
    artifact_dir: Path,
    device_identity: SimulatedDeviceIdentities,
) -> Workflow:
    source_media = artifact_dir / "source.webm"
    source_media.write_bytes(b"source media")
    return Workflow(
        extractor=RecordingExtractor(source_media),
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace",
        settings=JsonSettingsStore(artifact_dir / "settings.json"),
        device_identity=device_identity,
    )


def test_chosen_headphones_folder_survives_restart_and_transfer_uses_verified_device(
    artifact_dir: Path,
) -> None:
    headphones_folder = artifact_dir / "Volumes" / "SWIM" / "Music"
    headphones_folder.mkdir(parents=True)
    identity = HeadphonesDeviceIdentity(kind="macos-volume-uuid", stable_id="MAC-VOLUME-1")
    devices = SimulatedDeviceIdentities({headphones_folder: identity})

    first_run = _workflow(artifact_dir, devices)
    selection = first_run.choose_headphones_folder(headphones_folder)
    assert selection.ok is True

    after_restart = _workflow(artifact_dir, devices)
    result = after_restart.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result.ok is True
    assert result.outcomes[0].transfer_path == headphones_folder / "Lane Mix--a63668cb.mp3"


def test_reused_windows_drive_letter_on_different_device_is_rejected_before_extraction(
    artifact_dir: Path,
) -> None:
    headphones_folder = artifact_dir / "E" / "Music"
    headphones_folder.mkdir(parents=True)
    original = HeadphonesDeviceIdentity(kind="windows-volume-serial", stable_id="WIN-SERIAL-1")
    replacement = HeadphonesDeviceIdentity(kind="windows-volume-serial", stable_id="WIN-SERIAL-2")
    store = JsonSettingsStore(artifact_dir / "settings.json")
    setup = _workflow(artifact_dir, SimulatedDeviceIdentities({headphones_folder: original}))
    assert setup.choose_headphones_folder(headphones_folder).ok is True

    extractor = RecordingExtractor(artifact_dir / "unused.webm")
    workflow = Workflow(
        extractor=extractor,
        encoder=WritingEncoder(),
        workspace=artifact_dir / "workspace2",
        settings=store,
        device_identity=SimulatedDeviceIdentities({headphones_folder: replacement}),
    )
    result = workflow.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result.ok is False
    assert extractor.calls == []
    assert result.outcomes[0].status is TransferStatus.ERROR
    assert "reselect" in result.outcomes[0].message.lower()
    assert list(headphones_folder.iterdir()) == []


def test_changed_windows_drive_letter_with_same_identity_is_reconciled_before_transfer(
    artifact_dir: Path,
) -> None:
    old_folder = artifact_dir / "E" / "Music"
    new_folder = artifact_dir / "F" / "Music"
    old_folder.mkdir(parents=True)
    new_folder.mkdir(parents=True)
    identity = HeadphonesDeviceIdentity(kind="windows-volume-serial", stable_id="WIN-SERIAL-1")
    setup = _workflow(artifact_dir, SimulatedDeviceIdentities({old_folder: identity}))
    assert setup.choose_headphones_folder(old_folder).ok is True
    old_folder.rmdir()

    result = _workflow(
        artifact_dir,
        SimulatedDeviceIdentities({new_folder: identity}),
    ).transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result.ok is True
    assert result.outcomes[0].transfer_path == new_folder / "Lane Mix--a63668cb.mp3"
    assert JsonSettingsStore(artifact_dir / "settings.json").load_headphones_folder().path == new_folder


def test_unverifiable_identity_requires_explicit_reselect_before_write(
    artifact_dir: Path,
) -> None:
    old_folder = artifact_dir / "Volumes" / "SWIM" / "Music"
    new_folder = artifact_dir / "Volumes" / "SWIM2" / "Music"
    old_folder.mkdir(parents=True)
    new_folder.mkdir(parents=True)
    old_identity = HeadphonesDeviceIdentity(kind="macos-volume-uuid", stable_id="MAC-VOLUME-1")
    new_identity = HeadphonesDeviceIdentity(kind="macos-volume-uuid", stable_id="MAC-VOLUME-2")
    setup = _workflow(artifact_dir, SimulatedDeviceIdentities({old_folder: old_identity}))
    assert setup.choose_headphones_folder(old_folder).ok is True

    blocked = _workflow(
        artifact_dir,
        SimulatedDeviceIdentities({old_folder: None}),
    ).transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert blocked.ok is False
    assert "reselect" in blocked.outcomes[0].message.lower()
    assert list(old_folder.iterdir()) == []

    reselected = _workflow(artifact_dir, SimulatedDeviceIdentities({new_folder: new_identity}))
    assert reselected.choose_headphones_folder(new_folder).ok is True
    result = reselected.transfer_one("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result.ok is True
    assert result.outcomes[0].transfer_path == new_folder / "Lane Mix--a63668cb.mp3"
