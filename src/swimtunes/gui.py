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
        QLineEdit,
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

            self.url_input = QLineEdit()
            self.url_input.setPlaceholderText("Paste one public YouTube URL")
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
            layout.addWidget(QLabel("Source Item URL"))
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
            result = self.workflow.transfer_one(self.url_input.text(), self.headphones_folder)
            lines = []
            for outcome in result.outcomes:
                if outcome.status is TransferStatus.TRANSFERRED:
                    lines.append(f"Transferred: {outcome.title} -> {outcome.transfer_path}")
                else:
                    label = outcome.title or outcome.source_url or "Source Item"
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
