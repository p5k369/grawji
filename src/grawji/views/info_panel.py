"""The sidebar's Info tab."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from grawji.views.navigator import Navigator
from grawji.views.textures import texture_for_pixbuf

_INFO_PAGE = "info"


class InfoPanel:
    """Shows the open image's original and its EXIF."""

    def __init__(
        self,
        *,
        stack: Any,
        picture: Gtk.Picture,
        group: Adw.PreferencesGroup,
        navigator: Navigator,
    ) -> None:
        """Bind the panel to the sidebar's widgets.

        Args:
            stack: The sidebar stack the Info tab is a page of.
            picture: The thumbnail of the original.
            group: The group the EXIF rows are listed in.
            navigator: The visible-region overlay on the thumbnail.
        """
        self._stack = stack
        self._picture = picture
        self._group = group
        self._navigator = navigator
        self._pixbuf: Any | None = None
        self._dirty = False
        self._rows: list[Any] = []
        self._exif: list[tuple[str, str]] = []
        stack.connect("notify::visible-child", self._on_page_changed)

    def show_original(self, pixbuf: Any) -> None:
        """Take the image's original."""
        self._pixbuf = pixbuf
        if pixbuf is None:
            self._picture.set_paintable(None)
            self._dirty = False
        elif self._visible():
            self._picture.set_paintable(texture_for_pixbuf(pixbuf))
            self._dirty = False
        else:
            self._dirty = True

    def show_exif(self, rows: list[tuple[str, str]]) -> None:
        """List the image's EXIF as label and value pairs."""
        self._exif = list(rows)
        self._list(self._exif)

    def set_developed_by(self, model: str | None) -> None:
        """Name the body that develops a foreign image."""
        extra = [("Developed by", model)] if model else []
        self._list(self._exif + extra)

    def _list(self, rows: list[tuple[str, str]]) -> None:
        """Replace the rows on show."""
        for row in self._rows:
            self._group.remove(row)
        self._rows = []
        for label, value in rows:
            row = Adw.ActionRow(title=label, subtitle=value)
            self._group.add(row)
            self._rows.append(row)

    def _visible(self) -> bool:
        """Whether the Info tab is showing."""
        return bool(self._stack.get_visible_child_name() == _INFO_PAGE)

    def _on_page_changed(self, *_args: object) -> None:
        """Build the deferred texture when the tab becomes visible."""
        if self._dirty and self._pixbuf is not None and self._visible():
            self._picture.set_paintable(texture_for_pixbuf(self._pixbuf))
            self._dirty = False
            self._navigator.queue_draw()
