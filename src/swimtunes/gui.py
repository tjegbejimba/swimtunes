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
        QCheckBox,
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
            self.playlist_entries = []
            self.playlist_checkboxes = []
            self.setWindowTitle("SwimTunes")

            self.url_input = QTextEdit()
            self.url_input.setAcceptRichText(False)
            self.url_input.setPlaceholderText("Paste public YouTube or SoundCloud URLs, one per line")
            self.folder_label = QLabel("No Headphones Folder selected")
            choose_button = QPushButton("Choose Headphones Folder")
            choose_button.clicked.connect(self.choose_headphones_folder)
            preview_button = QPushButton("Preview Playlist")
            preview_button.clicked.connect(self.preview)
            self.transfer_button = QPushButton("Transfer")
            self.transfer_button.clicked.connect(self.transfer)
            self.preview_container = QWidget()
            self.preview_layout = QVBoxLayout()
            self.preview_container.setLayout(self.preview_layout)
            self.output = QTextEdit()
            self.output.setReadOnly(True)

            folder_row = QHBoxLayout()
            folder_row.addWidget(self.folder_label, 1)
            folder_row.addWidget(choose_button)

            layout = QVBoxLayout()
            layout.addWidget(QLabel("Source Item URLs"))
            layout.addWidget(self.url_input)
            layout.addWidget(preview_button)
            layout.addWidget(self.preview_container)
            layout.addLayout(folder_row)
            layout.addWidget(self.transfer_button)
            layout.addWidget(self.output)
            container = QWidget()
            container.setLayout(layout)
            self.setCentralWidget(container)

        def choose_headphones_folder(self) -> None:
            selected = QFileDialog.getExistingDirectory(self, "Choose Headphones Folder")
            if selected:
                self.headphones_folder = Path(selected)
                self.folder_label.setText(selected)

        def preview(self) -> None:
            playlist = self.workflow.preview(self.url_input.toPlainText())
            self._clear_playlist_preview()
            if playlist.error:
                self.output.setPlainText(f"Error: {playlist.error}")
                self.transfer_button.setEnabled(True)
                return
            self.playlist_entries = list(playlist.entries)
            for entry in self.playlist_entries:
                label = entry.title if entry.available else f"{entry.title} — {entry.error or 'unavailable'}"
                checkbox = QCheckBox(label)
                checkbox.setChecked(False)
                checkbox.setEnabled(entry.available)
                checkbox.toggled.connect(self._update_transfer_enabled)
                self.playlist_checkboxes.append(checkbox)
                self.preview_layout.addWidget(checkbox)
            self.output.setPlainText(f"Preview: {playlist.title or 'Source Playlist'}")
            self._update_transfer_enabled()

        def transfer(self) -> None:
            if self.headphones_folder is None:
                self.output.setPlainText("Choose a Headphones Folder before starting.")
                return
            selected_entries = self._selected_playlist_entries()
            if self.playlist_entries:
                if not selected_entries:
                    self.output.setPlainText("Select at least one Source Item before starting.")
                    return
                items = selected_entries
                transfer = self.workflow.transfer_selected
            else:
                items = [
                    line.strip()
                    for line in self.url_input.toPlainText().splitlines()
                    if line.strip()
                ]
                if not items:
                    self.output.setPlainText("Paste at least one Source Item URL before starting.")
                    return
                transfer = self.workflow.run

            lines: list[str] = []
            transferred_labels: set[str] = set()

            def record_progress(outcome) -> None:
                label = outcome.title or outcome.source_url or "Source Item"
                if outcome.status is TransferStatus.DOWNLOADED:
                    lines.append(f"Downloaded: {label} -> {outcome.audio_path}")
                    self.output.setPlainText("\n".join(lines))
                elif outcome.status is TransferStatus.TRANSFERRED:
                    transferred_labels.add(label)
                    lines.append(f"Transferred: {label} -> {outcome.transfer_path}")
                    self.output.setPlainText("\n".join(lines))

            result = transfer(items, self.headphones_folder, on_progress=record_progress)
            for outcome in result.outcomes:
                label = outcome.title or outcome.source_url or "Source Item"
                if outcome.status is TransferStatus.TRANSFERRED:
                    if label not in transferred_labels:
                        lines.append(f"Transferred: {label} -> {outcome.transfer_path}")
                elif outcome.status is TransferStatus.ALREADY_PRESENT:
                    lines.append(f"Already present: {label} -> {outcome.transfer_path}")
                else:
                    lines.append(f"Error: {label}: {outcome.message}")
            self.output.setPlainText("\n".join(lines))

        def _clear_playlist_preview(self) -> None:
            while self.preview_layout.count():
                item = self.preview_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self.playlist_entries = []
            self.playlist_checkboxes = []

        def _selected_playlist_entries(self):
            return [
                entry
                for entry, checkbox in zip(self.playlist_entries, self.playlist_checkboxes, strict=False)
                if checkbox.isChecked()
            ]

        def _update_transfer_enabled(self) -> None:
            if self.playlist_entries:
                self.transfer_button.setEnabled(bool(self._selected_playlist_entries()))
            else:
                self.transfer_button.setEnabled(True)

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
