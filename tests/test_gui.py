from pathlib import Path

from swimtunes.models import (
    LocalAudioFile,
    LocalLibrarySettings,
    OperationResult,
    TransferOutcome,
    TransferRecord,
    TransferStatus,
)


class FakeWorkflow:
    def __init__(self) -> None:
        self.calls = []

    def run(self, items, headphones_folder: Path, on_progress=None):
        self.calls.append((list(items), headphones_folder))
        if on_progress is not None:
            on_progress(
                TransferOutcome(
                    status=TransferStatus.DOWNLOADED,
                    title="Lane Mix",
                    source_url=items[0],
                    audio_path=Path("Lane Mix.mp3"),
                    message="Downloaded Audio File",
                )
            )
        return OperationResult(
            [
                TransferOutcome(
                    status=TransferStatus.TRANSFERRED,
                    title="Lane Mix",
                    source_url=items[0],
                    transfer_path=headphones_folder / "Lane Mix.mp3",
                    message="Transferred",
                ),
                TransferOutcome(
                    status=TransferStatus.ERROR,
                    source_url=items[1],
                    message="Enter a public YouTube or SoundCloud URL.",
                ),
            ]
        )


def test_gui_accepts_multiline_input_and_shows_per_item_status(
    artifact_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QTextEdit
    from swimtunes.gui import _build_main_window

    app = QApplication.instance() or QApplication([])
    workflow = FakeWorkflow()
    window = _build_main_window()(workflow)
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()
    window.headphones_folder = headphones_folder
    window.url_input.setPlainText(
        "https://www.youtube.com/watch?v=abc123\n\nhttps://example.com/nope\n"
    )

    window.transfer()

    assert isinstance(window.url_input, QTextEdit)
    assert workflow.calls == [
        (
            ["https://www.youtube.com/watch?v=abc123", "https://example.com/nope"],
            headphones_folder,
        )
    ]
    output = window.output.toPlainText()
    assert "Downloaded: Lane Mix" in output
    assert "Transferred: Lane Mix" in output
    assert "Error: https://example.com/nope" in output
    window.close()
    app.processEvents()


def test_gui_lists_pending_transfers_and_retries_them(
    artifact_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton
    from swimtunes.gui import _build_main_window

    app = QApplication.instance() or QApplication([])
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()

    class PendingWorkflow:
        settings = None

        def __init__(self) -> None:
            self.retry_calls = []
            self._pending = [
                TransferRecord(
                    source_id="yt:test-video",
                    title="Lane Mix",
                    filename="Lane Mix--a63668cb.mp3",
                    byte_size=12,
                    sha256="abc",
                    source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    status="pending",
                )
            ]

        def pending_transfers(self):
            return list(self._pending)

        def retry_pending(self, source_id: str, headphones_folder: Path, on_progress=None, confirm_repair=False):
            self.retry_calls.append((source_id, headphones_folder, confirm_repair))
            self._pending = []
            return OperationResult(
                [
                    TransferOutcome(
                        status=TransferStatus.TRANSFERRED,
                        title="Lane Mix",
                        transfer_path=headphones_folder / "Lane Mix--a63668cb.mp3",
                    )
                ]
            )

    workflow = PendingWorkflow()
    window = _build_main_window()(workflow)
    window.headphones_folder = headphones_folder

    buttons = window.pending_container.findChildren(QPushButton)
    assert [button.text() for button in buttons] == ["Retry"]

    buttons[0].click()

    assert workflow.retry_calls == [("yt:test-video", headphones_folder, False)]
    assert "Transferred: Lane Mix" in window.output.toPlainText()
    assert window.pending_container.findChildren(QPushButton) == []
    window.close()
    app.processEvents()


def test_gui_confirms_repair_before_overwriting_damaged_pending_transfer(
    artifact_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
    from swimtunes.gui import _build_main_window

    app = QApplication.instance() or QApplication([])
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()

    class RepairWorkflow:
        settings = None

        def __init__(self) -> None:
            self.retry_calls = []

        def pending_transfers(self):
            return [
                TransferRecord(
                    source_id="yt:test-video",
                    title="Lane Mix",
                    filename="Lane Mix--a63668cb.mp3",
                    byte_size=12,
                    sha256="abc",
                    status="pending",
                )
            ]

        def retry_pending(self, source_id: str, headphones_folder: Path, on_progress=None, confirm_repair=False):
            self.retry_calls.append(confirm_repair)
            if not confirm_repair:
                return OperationResult(
                    [
                        TransferOutcome(
                            status=TransferStatus.REPAIR_NEEDED,
                            title="Lane Mix",
                            message="Damaged app-owned copy requires confirmation before repair.",
                        )
                    ]
                )
            return OperationResult(
                [TransferOutcome(status=TransferStatus.TRANSFERRED, title="Lane Mix")]
            )

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    workflow = RepairWorkflow()
    window = _build_main_window()(workflow)
    window.headphones_folder = headphones_folder

    window.pending_container.findChildren(QPushButton)[0].click()

    assert workflow.retry_calls == [False, True]
    assert "Transferred: Lane Mix" in window.output.toPlainText()
    window.close()
    app.processEvents()


def test_gui_persists_keep_local_toggle_and_lists_local_audio_for_later_transfer(
    artifact_dir: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton
    from swimtunes.gui import _build_main_window

    app = QApplication.instance() or QApplication([])
    local_library = artifact_dir / "local-library"
    local_library.mkdir()
    headphones_folder = artifact_dir / "headphones"
    headphones_folder.mkdir()

    class LocalLibraryWorkflow:
        settings = None

        def __init__(self) -> None:
            self.local_settings = LocalLibrarySettings(enabled=False, path=None)
            self.configure_calls = []
            self.transfer_local_calls = []

        def pending_transfers(self):
            return []

        def local_library_settings(self):
            return self.local_settings

        def configure_local_library(self, enabled: bool, path: Path | None, headphones_folder: Path | None = None):
            self.configure_calls.append((enabled, path, headphones_folder))
            self.local_settings = LocalLibrarySettings(enabled=enabled, path=path if enabled else None)
            return OperationResult(
                [
                    TransferOutcome(
                        status=TransferStatus.TRANSFERRED,
                        message="Local Library settings saved.",
                    )
                ]
            )

        def local_audio_files(self):
            if not self.local_settings.enabled or self.local_settings.path is None:
                return []
            return [
                LocalAudioFile(
                    source_id="yt:test-video",
                    title="Lane Mix",
                    source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    path=self.local_settings.path / "Lane Mix--a63668cb.mp3",
                    byte_size=20,
                    sha256="abc",
                )
            ]

        def transfer_local_audio(self, source_id: str, headphones_folder: Path, on_progress=None):
            self.transfer_local_calls.append((source_id, headphones_folder))
            return OperationResult(
                [
                    TransferOutcome(
                        status=TransferStatus.TRANSFERRED,
                        title="Lane Mix",
                        transfer_path=headphones_folder / "Lane Mix--a63668cb.mp3",
                    )
                ]
            )

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(local_library))
    workflow = LocalLibraryWorkflow()
    window = _build_main_window()(workflow)
    window.headphones_folder = headphones_folder

    assert window.keep_local_checkbox.isChecked() is False

    window.keep_local_checkbox.setChecked(True)

    assert workflow.configure_calls == [(True, local_library, headphones_folder)]
    assert "Local Library: " in window.local_library_label.text()
    buttons = window.local_library_container.findChildren(QPushButton)
    assert [button.text() for button in buttons] == ["Transfer"]

    buttons[0].click()

    assert workflow.transfer_local_calls == [("yt:test-video", headphones_folder)]
    assert "Transferred: Lane Mix" in window.output.toPlainText()
    window.close()
    app.processEvents()
