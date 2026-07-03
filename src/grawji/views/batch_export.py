"""Modal dialog driving a batch export: options, live progress, summary."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

_UI = (
    resources.files("grawji")
    .joinpath("ui", "batch_export.ui")
    .read_text(encoding="utf-8")
)


@Gtk.Template(string=_UI)
class BatchExportDialog(Adw.Dialog):
    """Confirm batch options, show progress, then a completion summary."""

    __gtype_name__ = "GrawjiBatchExportDialog"

    stack = Gtk.Template.Child()
    intro_label = Gtk.Template.Child()
    overwrite_row = Gtk.Template.Child()
    foreign_row = Gtk.Template.Child()
    export_button = Gtk.Template.Child()
    cancel_options_button = Gtk.Template.Child()
    progress = Gtk.Template.Child()
    file_label = Gtk.Template.Child()
    cancel_button = Gtk.Template.Child()
    summary_label = Gtk.Template.Child()
    close_button = Gtk.Template.Child()

    def __init__(
        self,
        *,
        count: int,
        overwrite: bool,
        on_start: Callable[[bool, bool], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """Wire the dialog to its options and intent callbacks."""
        super().__init__()
        self._on_start = on_start
        self._on_cancel = on_cancel

        images = "image" if count == 1 else "images"
        self.intro_label.set_text(
            f"Export {count} {images} to the chosen folder using the "
            "current recipe."
        )
        self.overwrite_row.set_active(overwrite)
        self.export_button.connect("clicked", self._on_export_clicked)
        self.cancel_options_button.connect("clicked", lambda *_a: self.close())
        self.cancel_button.connect("clicked", self._on_cancel_clicked)
        self.close_button.connect("clicked", lambda *_a: self.close())

    def _on_export_clicked(self, _button: Any) -> None:
        """Lock the dialog open and hand the options back to the caller."""
        self.set_can_close(False)  # only Cancel leaves a running export.
        self.stack.set_visible_child_name("progress")
        self._on_start(
            self.overwrite_row.get_active(), self.foreign_row.get_active()
        )

    def _on_cancel_clicked(self, _button: Any) -> None:
        """Ask the caller to stop; the batch ends after the current image."""
        self.cancel_button.set_sensitive(False)
        self.cancel_button.set_label("Cancelling…")
        self._on_cancel()

    def update(self, done: int, total: int, name: str) -> None:
        """Advance the progress bar to done/total, naming the next file."""
        fraction = done / total if total else 0.0
        self.progress.set_fraction(fraction)
        self.progress.set_text(f"{done} / {total}")
        self.file_label.set_text(name)

    def finish(self, summary: str) -> None:
        """Switch to the summary page; the export has ended."""
        self.set_can_close(True)
        self.summary_label.set_text(summary)
        self.stack.set_visible_child_name("summary")
