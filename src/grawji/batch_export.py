"""Modal dialog driving a batch export: options, live progress, summary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk, Pango


class BatchExportDialog(Adw.Dialog):
    """Confirm batch options, show progress, then a completion summary."""

    __gtype_name__ = "GrawjiBatchExportDialog"

    def __init__(
        self,
        *,
        count: int,
        overwrite: bool,
        on_start: Callable[[bool, bool], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """Build the dialog on its options page."""
        super().__init__(title="Batch export")
        self._on_start = on_start
        self._on_cancel = on_cancel
        self.set_content_width(420)

        self._stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE
        )
        self._stack.add_named(self._build_options(count, overwrite), "options")
        self._stack.add_named(self._build_progress(), "progress")
        self._stack.add_named(self._build_summary(), "summary")
        self.set_child(self._stack)

    def _build_options(self, count: int, overwrite: bool) -> Gtk.Widget:
        """Build the confirmation page with the two export switches."""
        box = _page_box()
        images = "image" if count == 1 else "images"
        box.append(
            _wrapped_label(
                f"Export {count} {images} to the chosen folder using the "
                "current recipe."
            )
        )
        group = Adw.PreferencesGroup()
        self._overwrite_row = Adw.SwitchRow(
            title="Overwrite existing files",
            subtitle="Off skips images already exported, to resume a batch.",
            active=overwrite,
        )
        self._foreign_row = Adw.SwitchRow(
            title="Skip RAFs from other cameras",
            subtitle="Carry on past files the connected body cannot convert.",
            active=True,
        )
        group.add(self._overwrite_row)
        group.add(self._foreign_row)
        box.append(group)

        buttons = _button_box()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_a: self.close())
        export = Gtk.Button(label="Export")
        export.add_css_class("suggested-action")
        export.connect("clicked", self._on_export_clicked)
        buttons.append(cancel)
        buttons.append(export)
        box.append(buttons)
        return box

    def _build_progress(self) -> Gtk.Widget:
        """Build the progress page with a bar, a filename and Cancel."""
        box = _page_box()
        self._progress = Gtk.ProgressBar(show_text=True)
        self._file_label = Gtk.Label(
            xalign=0.0, ellipsize=Pango.EllipsizeMode.END
        )
        self._file_label.add_css_class("dim-label")
        box.append(self._progress)
        box.append(self._file_label)

        buttons = _button_box()
        self._cancel_button = Gtk.Button(label="Cancel")
        self._cancel_button.add_css_class("destructive-action")
        self._cancel_button.connect("clicked", self._on_cancel_clicked)
        buttons.append(self._cancel_button)
        box.append(buttons)
        return box

    def _build_summary(self) -> Gtk.Widget:
        """Build the completion page with a summary label and Close."""
        box = _page_box()
        self._summary_label = _wrapped_label("")
        box.append(self._summary_label)
        buttons = _button_box()
        close = Gtk.Button(label="Close")
        close.add_css_class("suggested-action")
        close.connect("clicked", lambda *_a: self.close())
        buttons.append(close)
        box.append(buttons)
        return box

    def _on_export_clicked(self, _button: Any) -> None:
        """Lock the dialog open and hand the options back to the caller."""
        self.set_can_close(False)  # only Cancel leaves a running export.
        self._stack.set_visible_child_name("progress")
        self._on_start(
            self._overwrite_row.get_active(), self._foreign_row.get_active()
        )

    def _on_cancel_clicked(self, _button: Any) -> None:
        """Ask the caller to stop; the batch ends after the current image."""
        self._cancel_button.set_sensitive(False)
        self._cancel_button.set_label("Cancelling…")
        self._on_cancel()

    def update(self, done: int, total: int, name: str) -> None:
        """Advance the progress bar to done/total, naming the next file."""
        fraction = done / total if total else 0.0
        self._progress.set_fraction(fraction)
        self._progress.set_text(f"{done} / {total}")
        self._file_label.set_text(name)

    def finish(self, summary: str) -> None:
        """Switch to the summary page; the export has ended."""
        self.set_can_close(True)
        self._summary_label.set_text(summary)
        self._stack.set_visible_child_name("summary")


def _page_box() -> Gtk.Box:
    """Return a padded vertical box used for each dialog page."""
    return Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=18,
        margin_top=24,
        margin_bottom=24,
        margin_start=24,
        margin_end=24,
    )


def _button_box() -> Gtk.Box:
    """Return an end-aligned horizontal box for a page's action buttons."""
    return Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=6,
        halign=Gtk.Align.END,
    )


def _wrapped_label(text: str) -> Gtk.Label:
    """Return a left-aligned, wrapping label for body text."""
    return Gtk.Label(label=text, xalign=0.0, wrap=True)
