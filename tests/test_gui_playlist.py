from pathlib import Path

from swimtunes.models import SourcePlaylist, SourcePlaylistEntry


def test_playlist_preview_gui_starts_all_entries_unchecked_and_disables_zero_selection(monkeypatch, artifact_dir: Path) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from swimtunes.gui import _build_main_window

    app = QApplication.instance() or QApplication([])

    class PreviewWorkflow:
        def preview(self, url: str) -> SourcePlaylist:
            return SourcePlaylist(
                url=url,
                title="Morning Set",
                entries=[
                    SourcePlaylistEntry("yt:1", "https://www.youtube.com/watch?v=1", "Lane One"),
                    SourcePlaylistEntry(
                        "yt:gone",
                        "https://www.youtube.com/watch?v=gone",
                        "Deleted video",
                        available=False,
                        error="unavailable",
                    ),
                ],
            )

    window = _build_main_window()(workflow=PreviewWorkflow())
    window.url_input.setText("https://www.youtube.com/playlist?list=PL123")

    window.preview()

    assert [checkbox.text() for checkbox in window.playlist_checkboxes] == ["Lane One", "Deleted video — unavailable"]
    assert [checkbox.isChecked() for checkbox in window.playlist_checkboxes] == [False, False]
    assert window.transfer_button.isEnabled() is False

    window.playlist_checkboxes[0].setChecked(True)

    assert window.transfer_button.isEnabled() is True
    app.processEvents()
