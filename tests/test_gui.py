from pathlib import Path

from swimtunes.models import OperationResult, TransferOutcome, TransferStatus


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
