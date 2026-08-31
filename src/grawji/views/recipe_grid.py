"""Recipe tryout grid: the open image rendered once per picked recipe.

The dialog opens on a checklist of the saved recipes, grouped by their
library folders.
Render walks the picked entries sequentially through the camera engine,
downloading only thumbnails, and fills a card grid as previews arrive.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GdkPixbuf, GLib, Gtk, Pango

from grawji.imaging.render import texture_for_pixbuf
from grawji.imaging.thumbnails import orient_exif
from grawji.recipe import Recipe

_UI = (
    resources.files("grawji")
    .joinpath("ui", "recipe_grid.ui")
    .read_text(encoding="utf-8")
)

# Card preview width in pixels.
_CARD_WIDTH = 360

RenderFn = Callable[
    [Recipe, Callable[[bytes], None], Callable[[Exception], None]], None
]


@Gtk.Template(string=_UI)
class RecipeGridDialog(Adw.Dialog):
    """Pick recipes, then a progressively filled grid of previews."""

    __gtype_name__ = "GrawjiRecipeGridDialog"

    stack = Gtk.Template.Child()
    pick_box = Gtk.Template.Child()
    grid = Gtk.Template.Child()
    progress_label = Gtk.Template.Child()
    start_button = Gtk.Template.Child()
    stop_button = Gtk.Template.Child()

    def __init__(
        self,
        *,
        groups: list[tuple[str, list[tuple[str, str, Recipe]]]],
        render: RenderFn,
        on_pick: Callable[[str], None],
        orientation: int = 1,
    ) -> None:
        """Create the dialog on its grouped recipe checklist."""
        super().__init__()
        self._entries = [entry for _t, members in groups for entry in members]
        self._render = render
        self._on_pick = on_pick
        self._orientation = orientation
        self._queue: list[tuple[str, str, Recipe]] = []
        self._index = 0
        self._closed = False
        self._stopped = False
        self.connect("closed", self._on_closed)
        self._syncing_master = False
        master_row = Adw.ActionRow(title="All recipes")
        self._master = Gtk.CheckButton()
        self._master.set_active(True)
        master_row.add_prefix(self._master)
        master_row.set_activatable_widget(self._master)
        master_list = Gtk.ListBox()
        master_list.set_selection_mode(Gtk.SelectionMode.NONE)
        master_list.add_css_class("boxed-list")
        master_list.append(master_row)
        self.pick_box.append(master_list)

        self._checks: list[Gtk.CheckButton] = []
        for title, members in groups:
            if not members:
                continue
            group = Adw.PreferencesGroup()
            if title:
                group.set_title(GLib.markup_escape_text(title))
            for _key, label, _recipe in members:
                row = Adw.ActionRow()
                row.set_use_markup(False)
                row.set_title(label)
                check = Gtk.CheckButton()
                check.set_active(True)
                row.add_prefix(check)
                row.set_activatable_widget(check)
                group.add(row)
                self._checks.append(check)
            self.pick_box.append(group)
        self._sync_master()
        for check in self._checks:
            check.connect("toggled", lambda *_a: self._sync_master())
        self._master.connect("toggled", self._on_master_toggled)

        self.start_button.connect("clicked", lambda *_a: self._on_start())
        self.stop_button.connect("clicked", lambda *_a: self._on_stop())

    def _on_master_toggled(self, master: Gtk.CheckButton) -> None:
        """Check or uncheck every recipe row from the master row."""
        if self._syncing_master:
            return
        active = master.get_active()
        master.set_inconsistent(False)
        self._syncing_master = True
        for check in self._checks:
            check.set_active(active)
        self._syncing_master = False
        self._sync_master()

    def _sync_master(self) -> None:
        """Mirror the rows on the master check and count."""
        if self._syncing_master:
            return
        count = sum(1 for check in self._checks if check.get_active())
        self._syncing_master = True
        self._master.set_inconsistent(0 < count < len(self._checks))
        self._master.set_active(count == len(self._checks))
        self._syncing_master = False
        self.progress_label.set_label(
            f"{count} of {len(self._checks)} recipes picked"
        )

    def _on_closed(self, *_args: object) -> None:
        """Stop after the in-flight render once the dialog is gone."""
        self._closed = True

    def _on_start(self) -> None:
        """Render the picked recipes into the grid."""
        self._queue = [
            entry
            for entry, check in zip(self._entries, self._checks, strict=True)
            if check.get_active()
        ]
        if not self._queue:
            self.progress_label.set_label("Nothing picked.")
            return
        self._index = 0
        self._stopped = False
        self.stack.set_visible_child_name("grid")
        self.start_button.set_visible(False)
        self.stop_button.set_visible(True)
        self.stop_button.set_sensitive(True)
        self._next()

    def _on_stop(self) -> None:
        """Bail out after the render currently on the camera."""
        self._stopped = True
        self.stop_button.set_sensitive(False)
        self.progress_label.set_label("Stopping…")

    def _next(self) -> None:
        """Render the next entry, or finish."""
        if self._closed:
            return
        if self._stopped or self._index >= len(self._queue):
            done = not self._stopped and self._index >= len(self._queue)
            self.progress_label.set_label("" if done else "Stopped.")
            self.stop_button.set_visible(False)
            return
        _key, label, recipe = self._queue[self._index]
        self.progress_label.set_label(
            f"Rendering {label}… ({self._index + 1}/{len(self._queue)})"
        )
        self._render(recipe, self._on_rendered, self._on_error)

    def _on_rendered(self, jpeg: bytes) -> None:
        """Add the finished card and continue with the next entry."""
        if self._closed:
            return
        key, label, _recipe = self._queue[self._index]
        self.grid.append(self._card(key, label, jpeg))
        self._index += 1
        self._next()

    def _on_error(self, exc: Exception) -> None:
        """Stop the run and surface the failure in the progress line."""
        if not self._closed:
            self.progress_label.set_label(f"Render failed: {exc}")
            self.stop_button.set_visible(False)

    def _card(self, key: str, label: str, jpeg: bytes) -> Gtk.Button:
        """Build one clickable preview card."""
        picture = Gtk.Picture()
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        pixbuf = _decode_scaled(jpeg, _CARD_WIDTH, self._orientation)
        picture.set_paintable(texture_for_pixbuf(pixbuf))
        picture.set_size_request(-1, pixbuf.get_height())
        caption = Gtk.Label(label=label)
        caption.set_ellipsize(Pango.EllipsizeMode.END)
        caption.add_css_class("caption")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(picture)
        box.append(caption)
        button = Gtk.Button(child=box)
        button.add_css_class("card")
        button.set_tooltip_text(label)
        button.connect("clicked", self._on_card_clicked, key)
        return button

    def _on_card_clicked(self, _button: Gtk.Button, key: str) -> None:
        """Apply the picked recipe and dismiss the dialog."""
        self._on_pick(key)
        self.close()


def _decode_scaled(jpeg: bytes, width: int, orientation: int = 1) -> Any:
    """Decode a JPEG downscaled to width pixels, upright."""

    def on_size(loader: Any, w: int, h: int) -> None:
        if w > width:
            loader.set_size(width, max(1, round(h * width / w)))

    loader = GdkPixbuf.PixbufLoader()
    loader.connect("size-prepared", on_size)
    loader.write(jpeg)
    loader.close()
    pixbuf = loader.get_pixbuf()
    if pixbuf.get_option("orientation") is not None:
        pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    else:
        pixbuf = orient_exif(pixbuf, orientation)
    return _trim_letterbox(pixbuf)


def _trim_letterbox(pixbuf: Any, threshold: int = 24) -> Any:
    """Cut near-black letterbox bars off every edge of a pixbuf."""
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    data = pixbuf.get_pixels()
    stride = pixbuf.get_rowstride()
    channels = pixbuf.get_n_channels()

    def row_dark(y: int) -> bool:
        row = data[y * stride : y * stride + width * channels]
        return max(row) < threshold

    def col_dark(x: int) -> bool:
        return all(
            max(
                data[y * stride + x * channels : y * stride + x * channels + 3]
            )
            < threshold
            for y in range(0, height, 4)
        )

    top = 0
    while top < height // 3 and row_dark(top):
        top += 1
    bottom = height
    while bottom > height * 2 // 3 and row_dark(bottom - 1):
        bottom -= 1
    left = 0
    while left < width // 3 and col_dark(left):
        left += 1
    right = width
    while right > width * 2 // 3 and col_dark(right - 1):
        right -= 1
    if (left, top, right, bottom) == (0, 0, width, height):
        return pixbuf
    return pixbuf.new_subpixbuf(left, top, right - left, bottom - top)
