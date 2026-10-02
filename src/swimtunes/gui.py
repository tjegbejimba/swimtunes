from __future__ import annotations

import sys
from pathlib import Path

from swimtunes.app import Workflow
from swimtunes.models import TransferStatus
from swimtunes.tools import self_check


class MainWindow:  # populated at runtime after PySide6 import succeeds
    pass


def _build_main_window():
    from PySide6.QtWidgets import (
        QFileDialog,
        QHBoxLayout,
        QCheckBox,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    class SwimTunesWindow(QMainWindow):
        def __init__(self, workflow: Workflow | None = None) -> None:
            super().__init__()
            self.workflow = workflow or Workflow()
            selection = None
            settings = getattr(self.workflow, "settings", None)
            if settings is not None:
                selection = settings.load_headphones_folder()
            self.headphones_folder: Path | None = selection.path if selection else None
            self.playlist_entries = []
            self.playlist_checkboxes = []
            self.setWindowTitle("SwimTunes")

            self.url_input = QTextEdit()
            self.url_input.setAcceptRichText(False)
            self.url_input.setPlaceholderText("Paste public YouTube or SoundCloud URLs, one per line")
            self.folder_label = QLabel(
                str(self.headphones_folder)
                if self.headphones_folder
                else "No Headphones Folder selected"
            )
            choose_button = QPushButton("Choose Headphones Folder")
            choose_button.clicked.connect(self.choose_headphones_folder)
            preview_button = QPushButton("Preview Playlist")
            preview_button.clicked.connect(self.preview)
            self.transfer_button = QPushButton("Transfer")
            self.transfer_button.clicked.connect(self.transfer)
            self.preview_container = QWidget()
            self.preview_layout = QVBoxLayout()
            self.preview_container.setLayout(self.preview_layout)
            self.pending_container = QWidget()
            self.pending_layout = QVBoxLayout()
            self.pending_container.setLayout(self.pending_layout)
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
            layout.addWidget(QLabel("Pending Transfers"))
            layout.addWidget(self.pending_container)
            layout.addLayout(folder_row)
            layout.addWidget(self.transfer_button)
            layout.addWidget(self.output)
            container = QWidget()
            container.setLayout(layout)
            self.setCentralWidget(container)
            self._refresh_pending_transfers()

        def choose_headphones_folder(self) -> None:
            selected = QFileDialog.getExistingDirectory(self, "Choose Headphones Folder")
            if selected:
                result = self.workflow.choose_headphones_folder(Path(selected))
                if result.ok:
                    self.headphones_folder = Path(selected)
                    self.folder_label.setText(selected)
                    self.output.setPlainText("Headphones Folder selected.")
                else:
                    self.output.setPlainText(result.outcomes[0].message)

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
            self._append_final_outcomes(result.outcomes, lines, transferred_labels)
            self.output.setPlainText("\n".join(lines))
            self._refresh_pending_transfers()

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

        def _refresh_pending_transfers(self) -> None:
            while self.pending_layout.count():
                item = self.pending_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
            pending_getter = getattr(self.workflow, "pending_transfers", None)
            if pending_getter is None:
                return
            for record in pending_getter():
                row = QWidget()
                row_layout = QHBoxLayout()
                row.setLayout(row_layout)
                row_layout.addWidget(QLabel(record.title), 1)
                retry_button = QPushButton("Retry")
                retry_button.clicked.connect(
                    lambda checked=False, source_id=record.source_id: self._retry_pending_transfer(source_id)
                )
                row_layout.addWidget(retry_button)
                self.pending_layout.addWidget(row)

        def _retry_pending_transfer(self, source_id: str, confirm_repair: bool = False) -> None:
            if self.headphones_folder is None:
                self.output.setPlainText("Choose a Headphones Folder before starting.")
                return
            lines: list[str] = []
            transferred_labels: set[str] = set()
            result = self.workflow.retry_pending(
                source_id,
                self.headphones_folder,
                on_progress=lambda outcome: self._record_progress(outcome, lines, transferred_labels),
                confirm_repair=confirm_repair,
            )
            if (
                result.outcomes
                and result.outcomes[0].status is TransferStatus.REPAIR_NEEDED
                and not confirm_repair
            ):
                answer = QMessageBox.question(
                    self,
                    "Repair Transfer?",
                    result.outcomes[0].message,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._retry_pending_transfer(source_id, confirm_repair=True)
                else:
                    self.output.setPlainText(result.outcomes[0].message)
                return
            self._append_final_outcomes(result.outcomes, lines, transferred_labels)
            self.output.setPlainText("\n".join(lines))
            self._refresh_pending_transfers()

        def _record_progress(self, outcome, lines: list[str], transferred_labels: set[str]) -> None:
            label = outcome.title or outcome.source_url or "Source Item"
            if outcome.status is TransferStatus.DOWNLOADED:
                lines.append(f"Downloaded: {label} -> {outcome.audio_path}")
                self.output.setPlainText("\n".join(lines))
            elif outcome.status is TransferStatus.TRANSFERRED:
                transferred_labels.add(label)
                lines.append(f"Transferred: {label} -> {outcome.transfer_path}")
                self.output.setPlainText("\n".join(lines))

        def _append_final_outcomes(self, outcomes, lines: list[str], transferred_labels: set[str]) -> None:
            for outcome in outcomes:
                label = outcome.title or outcome.source_url or "Source Item"
                if outcome.status is TransferStatus.TRANSFERRED:
                    if label not in transferred_labels:
                        lines.append(f"Transferred: {label} -> {outcome.transfer_path}")
                elif outcome.status is TransferStatus.ALREADY_PRESENT:
                    lines.append(f"Already present: {label} -> {outcome.transfer_path}")
                elif outcome.status is TransferStatus.REPAIR_NEEDED:
                    lines.append(f"Repair needed: {label}: {outcome.message}")
                else:
                    lines.append(f"Error: {label}: {outcome.message}")

    return SwimTunesWindow


def main() -> int:
    if "--self-check" in sys.argv:
        report = self_check()
        if report.ok:
            return 0
        print(report.message, file=sys.stderr)
        return 1

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    window_class = _build_main_window()
    window = window_class()
    window.resize(640, 360)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
