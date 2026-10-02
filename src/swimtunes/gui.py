from __future__ import annotations

import sys
import inspect
import subprocess
from pathlib import Path

from swimtunes.app import SimpleCancelToken, Workflow
from swimtunes.models import SelectionSpaceSummary, TransferStatus
from swimtunes.tools import self_check
from swimtunes.update import UpdateCheckStatus, default_update_checker
from swimtunes.update.installer import default_portable_layout, install_update_at_startup


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
    from PySide6.QtCore import QObject, Qt, QThread, Signal

    class TransferWorker(QObject):
        progress = Signal(object)
        finished = Signal(object)

        def __init__(self, transfer, items, headphones_folder: Path, cancel_token: SimpleCancelToken) -> None:
            super().__init__()
            self.transfer = transfer
            self.items = items
            self.headphones_folder = headphones_folder
            self.cancel_token = cancel_token

        def run(self) -> None:
            kwargs = {"on_progress": self.progress.emit}
            if "cancel_token" in inspect.signature(self.transfer).parameters:
                kwargs["cancel_token"] = self.cancel_token
            result = self.transfer(self.items, self.headphones_folder, **kwargs)
            self.finished.emit(result)

    class SwimTunesWindow(QMainWindow):
        def __init__(self, workflow: Workflow | None = None, update_checker=None) -> None:
            super().__init__()
            self.workflow = workflow or Workflow()
            self.update_checker = update_checker if update_checker is not None else default_update_checker()
            selection = None
            settings = getattr(self.workflow, "settings", None)
            if settings is not None:
                selection = settings.load_headphones_folder()
            self.headphones_folder: Path | None = selection.path if selection else None
            local_settings_getter = getattr(self.workflow, "local_library_settings", None)
            self.local_library_settings = (
                local_settings_getter() if local_settings_getter is not None else None
            )
            self.local_library_path: Path | None = (
                self.local_library_settings.path
                if self.local_library_settings is not None and self.local_library_settings.enabled
                else None
            )
            self.playlist_entries = []
            self.playlist_checkboxes = []
            self.cancel_token: SimpleCancelToken | None = None
            self.active_thread: QThread | None = None
            self.active_worker: TransferWorker | None = None
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
            self.keep_local_checkbox = QCheckBox("Keep local MP3s")
            self.keep_local_checkbox.setChecked(
                bool(self.local_library_settings and self.local_library_settings.enabled)
            )
            self.keep_local_checkbox.toggled.connect(self._toggle_keep_local)
            self.local_library_label = QLabel(
                f"Local Library: {self.local_library_path}"
                if self.local_library_path
                else "No Local Library selected"
            )
            choose_local_button = QPushButton("Choose Local Library")
            choose_local_button.clicked.connect(self.choose_local_library)
            preview_button = QPushButton("Preview Playlist")
            preview_button.clicked.connect(self.preview)
            self.transfer_button = QPushButton("Transfer")
            self.transfer_button.clicked.connect(self.transfer)
            self.cancel_button = QPushButton("Cancel")
            self.cancel_button.clicked.connect(self.cancel_transfer)
            self.cancel_button.setEnabled(False)
            self.preview_container = QWidget()
            self.preview_layout = QVBoxLayout()
            self.preview_container.setLayout(self.preview_layout)
            self.pending_container = QWidget()
            self.pending_layout = QVBoxLayout()
            self.pending_container.setLayout(self.pending_layout)
            self.local_library_container = QWidget()
            self.local_library_layout = QVBoxLayout()
            self.local_library_container.setLayout(self.local_library_layout)
            self.output = QTextEdit()
            self.output.setReadOnly(True)

            folder_row = QHBoxLayout()
            folder_row.addWidget(self.folder_label, 1)
            folder_row.addWidget(choose_button)
            local_row = QHBoxLayout()
            local_row.addWidget(self.local_library_label, 1)
            local_row.addWidget(choose_local_button)

            layout = QVBoxLayout()
            layout.addWidget(QLabel("Source Item URLs"))
            layout.addWidget(self.url_input)
            layout.addWidget(preview_button)
            layout.addWidget(self.preview_container)
            layout.addWidget(QLabel("Pending Transfers"))
            layout.addWidget(self.pending_container)
            layout.addLayout(folder_row)
            layout.addWidget(self.keep_local_checkbox)
            layout.addLayout(local_row)
            layout.addWidget(QLabel("Local Library Audio Files"))
            layout.addWidget(self.local_library_container)
            transfer_row = QHBoxLayout()
            transfer_row.addWidget(self.transfer_button)
            transfer_row.addWidget(self.cancel_button)
            layout.addLayout(transfer_row)
            layout.addWidget(self.output)
            container = QWidget()
            container.setLayout(layout)
            self.setCentralWidget(container)
            self._run_startup_update_check()
            self._refresh_pending_transfers()
            self._refresh_local_library_audio()

        def _run_startup_update_check(self) -> None:
            if self.update_checker is None:
                return
            result = self.update_checker.check()
            if result.status is UpdateCheckStatus.WARNING and result.warning:
                self.output.setPlainText(f"Update warning: {result.warning}")
                return
            if result.status is not UpdateCheckStatus.ELIGIBLE or result.candidate is None:
                return
            layout = default_portable_layout()
            if layout is None:
                self.output.setPlainText(
                    f"Update available: SwimTunes {result.candidate.version} is verified; portable layout was not detected."
                )
                return
            install_result = install_update_at_startup(layout=layout, update_result=result)
            if install_result.installed:
                self.output.setPlainText(f"Update installed: SwimTunes {install_result.version}; relaunching.")
                self._relaunch_from_launcher(layout.root)
            elif install_result.warning:
                self.output.setPlainText(f"Update warning: {install_result.warning}")

        def _relaunch_from_launcher(self, portable_root: Path) -> None:
            launcher = portable_root / "SwimTunes.exe"
            if not launcher.is_file():
                self.output.setPlainText(
                    f"Update installed, but launcher was not found at {launcher}. Please restart SwimTunes."
                )
                return
            subprocess.Popen([str(launcher)])
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.quit()

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

        def choose_local_library(self) -> None:
            selected = QFileDialog.getExistingDirectory(self, "Choose Local Library")
            if selected:
                self._save_local_library_settings(True, Path(selected))

        def _toggle_keep_local(self, checked: bool) -> None:
            if checked:
                if self.local_library_path is None:
                    selected = QFileDialog.getExistingDirectory(self, "Choose Local Library")
                    if not selected:
                        self.keep_local_checkbox.blockSignals(True)
                        self.keep_local_checkbox.setChecked(False)
                        self.keep_local_checkbox.blockSignals(False)
                        return
                    self._save_local_library_settings(True, Path(selected))
                else:
                    self._save_local_library_settings(True, self.local_library_path)
            else:
                self._save_local_library_settings(False, None)

        def _save_local_library_settings(self, enabled: bool, path: Path | None) -> None:
            configure = getattr(self.workflow, "configure_local_library", None)
            if configure is None:
                return
            result = configure(enabled, path, headphones_folder=self.headphones_folder)
            if result.ok:
                self.local_library_path = path if enabled else None
                self.local_library_label.setText(
                    f"Local Library: {self.local_library_path}"
                    if self.local_library_path
                    else "No Local Library selected"
                )
                self.output.setPlainText(result.outcomes[0].message)
                self.keep_local_checkbox.blockSignals(True)
                self.keep_local_checkbox.setChecked(enabled)
                self.keep_local_checkbox.blockSignals(False)
                self._refresh_local_library_audio()
            else:
                self.output.setPlainText(result.outcomes[0].message)
                self.keep_local_checkbox.blockSignals(True)
                self.keep_local_checkbox.setChecked(False)
                self.keep_local_checkbox.blockSignals(False)

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
            if self.headphones_folder is None and not self.keep_local_checkbox.isChecked():
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

            summary_getter = getattr(self.workflow, "selection_space_summary", None)
            summary = (
                summary_getter(items, self.headphones_folder)
                if summary_getter is not None
                else SelectionSpaceSummary(item_count=len(items), estimated_bytes=0, free_bytes=0)
            )
            summary_line = self._selection_summary_text(summary)
            if summary.known_too_large:
                self.output.setPlainText(f"{summary_line}\nRefusing to start: selection does not fit.")
                return
            if summary.has_uncertainty:
                answer = QMessageBox.warning(
                    self,
                    "Confirm Transfer",
                    f"{summary_line}\nSome sizes or free space are uncertain. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self.output.setPlainText(f"{summary_line}\nCancelled before starting.")
                    return

            lines: list[str] = []
            transferred_labels: set[str] = set()

            lines.append(summary_line)
            self.output.setPlainText("\n".join(lines))
            self._start_background_transfer(
                transfer,
                items,
                lines,
                transferred_labels,
                lambda outcome: self._record_progress(outcome, lines, transferred_labels),
            )

        def cancel_transfer(self) -> None:
            if self.cancel_token is not None:
                self.cancel_token.cancel()
                self.cancel_button.setEnabled(False)
                self.output.append("Cancelling…")

        def _start_background_transfer(
            self,
            transfer,
            items,
            lines: list[str],
            transferred_labels: set[str],
            record_progress,
        ) -> None:
            self.cancel_token = SimpleCancelToken()
            self.transfer_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            thread = QThread(self)
            worker = TransferWorker(transfer, items, self.headphones_folder, self.cancel_token)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.progress.connect(record_progress, Qt.ConnectionType.QueuedConnection)
            worker.finished.connect(
                lambda result: self._finish_background_transfer(result, lines, transferred_labels),
                Qt.ConnectionType.QueuedConnection,
            )
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            self.active_thread = thread
            self.active_worker = worker
            thread.start()

        def _finish_background_transfer(self, result, lines: list[str], transferred_labels: set[str]) -> None:
            self._append_final_outcomes(result.outcomes, lines, transferred_labels)
            self.output.setPlainText("\n".join(lines))
            self.transfer_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.cancel_token = None
            self.active_worker = None
            self.active_thread = None
            self._refresh_pending_transfers()
            self._refresh_local_library_audio()

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

        def _refresh_local_library_audio(self) -> None:
            while self.local_library_layout.count():
                item = self.local_library_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
            local_getter = getattr(self.workflow, "local_audio_files", None)
            if local_getter is None:
                return
            for audio in local_getter():
                row = QWidget()
                row_layout = QHBoxLayout()
                row.setLayout(row_layout)
                row_layout.addWidget(QLabel(audio.title), 1)
                transfer_button = QPushButton("Transfer")
                transfer_button.clicked.connect(
                    lambda checked=False, source_id=audio.source_id: self._transfer_local_audio(source_id)
                )
                row_layout.addWidget(transfer_button)
                self.local_library_layout.addWidget(row)

        def _transfer_local_audio(self, source_id: str) -> None:
            if self.headphones_folder is None:
                self.output.setPlainText("Choose a Headphones Folder before starting.")
                return
            lines: list[str] = []
            transferred_labels: set[str] = set()
            result = self.workflow.transfer_local_audio(
                source_id,
                self.headphones_folder,
                on_progress=lambda outcome: self._record_progress(outcome, lines, transferred_labels),
            )
            self._append_final_outcomes(result.outcomes, lines, transferred_labels)
            self.output.setPlainText("\n".join(lines))
            self._refresh_local_library_audio()

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

        def _selection_summary_text(self, summary: SelectionSpaceSummary) -> str:
            size = (
                f"{summary.estimated_bytes} bytes estimated"
                if summary.estimated_bytes is not None
                else "estimated size uncertain"
            )
            free = (
                f"{summary.free_bytes} bytes available"
                if summary.free_bytes is not None
                else "available space uncertain"
            )
            unknown = (
                f"; {summary.unknown_size_count} Source Item size(s) uncertain"
                if summary.unknown_size_count
                else ""
            )
            return f"Selection: {summary.item_count} Source Item(s), {size}, {free}{unknown}."

        def _append_final_outcomes(self, outcomes, lines: list[str], transferred_labels: set[str]) -> None:
            for outcome in outcomes:
                label = outcome.title or outcome.source_url or "Source Item"
                if outcome.status is TransferStatus.TRANSFERRED:
                    if label not in transferred_labels:
                        lines.append(f"Transferred: {label} -> {outcome.transfer_path}")
                elif outcome.status is TransferStatus.ALREADY_PRESENT:
                    lines.append(f"Already present: {label} -> {outcome.transfer_path}")
                elif outcome.status is TransferStatus.DOWNLOADED:
                    lines.append(f"Downloaded: {label} -> {outcome.audio_path}")
                elif outcome.status is TransferStatus.REPAIR_NEEDED:
                    lines.append(f"Repair needed: {label}: {outcome.message}")
                elif outcome.status is TransferStatus.CANCELLED:
                    lines.append(f"Cancelled: {label}: {outcome.message}")
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
