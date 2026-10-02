from __future__ import annotations

import sys
from pathlib import Path

from swimtunes.app import Workflow
from swimtunes.models import TransferStatus


class MainWindow:  # populated at runtime after PySide6 import succeeds
    pass


def _build_main_window():
    from PySide6.QtWidgets import (
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    class SwimTunesWindow(QMainWindow):
        def __init__(self, workflow: Workflow | None = None) -> None:
            super().__init__()
            self.workflow = workflow or Workflow()
            self.headphones_folder: Path | None = None
            self.setWindowTitle("SwimTunes")

            self.url_input = QTextEdit()
            self.url_input.setAcceptRichText(False)
            self.url_input.setPlaceholderText("Paste public YouTube or SoundCloud URLs, one per line")
            self.folder_label = QLabel("No Headphones Folder selected")
            choose_button = QPushButton("Choose Headphones Folder")
            choose_button.clicked.connect(self.choose_headphones_folder)
            transfer_button = QPushButton("Transfer")
            transfer_button.clicked.connect(self.transfer)
            self.output = QTextEdit()
            self.output.setReadOnly(True)

            folder_row = QHBoxLayout()
            folder_row.addWidget(self.folder_label, 1)
            folder_row.addWidget(choose_button)

            layout = QVBoxLayout()
            layout.addWidget(QLabel("Source Item URLs"))
            layout.addWidget(self.url_input)
            layout.addLayout(folder_row)
            layout.addWidget(transfer_button)
            layout.addWidget(self.output)
            container = QWidget()
            container.setLayout(layout)
            self.setCentralWidget(container)

        def choose_headphones_folder(self) -> None:
            selected = QFileDialog.getExistingDirectory(self, "Choose Headphones Folder")
            if selected:
                self.headphones_folder = Path(selected)
                self.folder_label.setText(selected)

        def transfer(self) -> None:
            if self.headphones_folder is None:
                self.output.setPlainText("Choose a Headphones Folder before starting.")
                return
            urls = [
                line.strip()
                for line in self.url_input.toPlainText().splitlines()
                if line.strip()
            ]
            if not urls:
                self.output.setPlainText("Paste at least one Source Item URL before starting.")
                return
            lines: list[str] = []

            transferred_labels: set[str] = set()

            def record_progress(outcome) -> None:
                if outcome.status is TransferStatus.DOWNLOADED:
                    label = outcome.title or outcome.source_url or "Source Item"
                    lines.append(f"Downloaded: {label} -> {outcome.audio_path}")
                    self.output.setPlainText("\n".join(lines))
                elif outcome.status is TransferStatus.TRANSFERRED:
                    label = outcome.title or outcome.source_url or "Source Item"
                    transferred_labels.add(label)
                    lines.append(f"Transferred: {label} -> {outcome.transfer_path}")
                    self.output.setPlainText("\n".join(lines))

            result = self.workflow.run(urls, self.headphones_folder, on_progress=record_progress)
            for outcome in result.outcomes:
                label = outcome.title or outcome.source_url or "Source Item"
                if outcome.status is TransferStatus.TRANSFERRED:
                    if label not in transferred_labels:
                        lines.append(f"Transferred: {label} -> {outcome.transfer_path}")
                else:
                    lines.append(f"Error: {label}: {outcome.message}")
            self.output.setPlainText("\n".join(lines))

    return SwimTunesWindow


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    window_class = _build_main_window()
    window = window_class()
    window.resize(640, 360)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
