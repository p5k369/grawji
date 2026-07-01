"""Overview navigator over a thumbnail of the preview."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk

# At/above this visible fraction the whole image shows, so no rectangle.
_FULL = 0.999
# Map a preview-space (fx, fy, fw, fh) fraction rect into the unrotated
# thumbnail space.
_RECT_ROTATIONS = {
    90: lambda fx, fy, fw, fh: (fy, 1 - fx - fw, fh, fw),
    180: lambda fx, fy, fw, fh: (1 - fx - fw, 1 - fy - fh, fw, fh),
    270: lambda fx, fy, fw, fh: (1 - fy - fh, fx, fh, fw),
}
# Inverse of the above for a single point.
_POINT_ROTATIONS = {
    90: lambda ix, iy: (1 - iy, ix),
    180: lambda ix, iy: (1 - ix, 1 - iy),
    270: lambda ix, iy: (iy, 1 - ix),
}


class Navigator:
    """A visible-region rectangle on a thumbnail; drag it to pan."""

    def __init__(
        self,
        *,
        area: Gtk.DrawingArea,
        scroll: Gtk.ScrolledWindow,
        picture: Gtk.Picture,
        get_rotation: Callable[[], int],
    ) -> None:
        """Wire the navigator to its drawing area and the preview scroller.

        Args:
            area: The drawing area overlaid on the thumbnail.
            scroll: The preview scroller whose visible region is shown.
            picture: The thumbnail picture.
            get_rotation: Returns the clockwise rotation baked into the
                preview, so the region maps onto the unrotated thumbnail.
        """
        self._area = area
        self._scroll = scroll
        self._picture = picture
        self._get_rotation = get_rotation

        area.set_draw_func(self._draw)
        for adj in (scroll.get_hadjustment(), scroll.get_vadjustment()):
            adj.connect("value-changed", self._on_adjustment)
            adj.connect("changed", self._on_adjustment)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_pressed)
        area.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self._on_drag)
        area.add_controller(drag)
        area.set_cursor(Gdk.Cursor.new_from_name("grab", None))

    def queue_draw(self) -> None:
        """Redraw the rectangle (e.g. after the thumbnail image changes)."""
        self._area.queue_draw()

    def _on_adjustment(self, _adj: Any) -> None:
        """Redraw after a scroll or zoom change."""
        self._area.queue_draw()

    def _on_pressed(
        self, _g: Gtk.GestureClick, _n: int, x: float, y: float
    ) -> None:
        """Pan the preview to the point clicked on the thumbnail."""
        self._pan_to(x, y)

    def _on_drag(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        """Pan the preview while dragging on the thumbnail."""
        ok, sx, sy = gesture.get_start_point()
        if ok:
            self._pan_to(sx + dx, sy + dy)

    def _pan_to(self, x: float, y: float) -> None:
        """Centre the preview viewport on the thumbnail point (x, y)."""
        paintable = self._picture.get_paintable()
        if paintable is None:
            return
        iw = paintable.get_intrinsic_width()
        ih = paintable.get_intrinsic_height()
        w, h = self._area.get_width(), self._area.get_height()
        if iw <= 0 or ih <= 0 or w <= 0 or h <= 0:
            return
        scale = min(w / iw, h / ih)
        dw, dh = iw * scale, ih * scale
        ox, oy = (w - dw) / 2, (h - dh) / 2
        ix = min(1.0, max(0.0, (x - ox) / dw))
        iy = min(1.0, max(0.0, (y - oy) / dh))
        transform = _POINT_ROTATIONS.get(self._get_rotation())
        px, py = transform(ix, iy) if transform else (ix, iy)
        self._center(self._scroll.get_hadjustment(), px)
        self._center(self._scroll.get_vadjustment(), py)

    @staticmethod
    def _center(adj: Any, frac: float) -> None:
        """Scroll adj so the given content fraction sits at the centre."""
        upper, page = adj.get_upper(), adj.get_page_size()
        target = frac * upper - page / 2
        adj.set_value(max(0.0, min(target, max(0.0, upper - page))))

    def _draw(self, _area: Any, cx: Any, width: int, height: int) -> None:
        """Outline the preview's visible region on the thumbnail."""
        paintable = self._picture.get_paintable()
        if paintable is None:
            return
        iw = paintable.get_intrinsic_width()
        ih = paintable.get_intrinsic_height()
        hadj = self._scroll.get_hadjustment()
        vadj = self._scroll.get_vadjustment()
        hu, vu = hadj.get_upper(), vadj.get_upper()
        if iw <= 0 or ih <= 0 or hu <= 0 or vu <= 0:
            return
        fx, fw = hadj.get_value() / hu, hadj.get_page_size() / hu
        fy, fh = vadj.get_value() / vu, vadj.get_page_size() / vu
        if fw >= _FULL and fh >= _FULL:
            return
        transform = _RECT_ROTATIONS.get(self._get_rotation())
        if transform:
            fx, fy, fw, fh = transform(fx, fy, fw, fh)

        # The thumbnail is content-fit=contain, so find the drawn image rect.
        scale = min(width / iw, height / ih)
        dw, dh = iw * scale, ih * scale
        ox, oy = (width - dw) / 2, (height - dh) / 2
        rx, ry = ox + fx * dw, oy + fy * dh
        rw, rh = fw * dw, fh * dh

        cx.set_source_rgba(0.0, 0.0, 0.0, 0.4)
        for bx, by, bw, bh in (
            (ox, oy, dw, ry - oy),
            (ox, ry + rh, dw, oy + dh - ry - rh),
            (ox, ry, rx - ox, rh),
            (rx + rw, ry, ox + dw - rx - rw, rh),
        ):
            cx.rectangle(bx, by, max(0.0, bw), max(0.0, bh))
            cx.fill()
        cx.set_source_rgba(1.0, 1.0, 1.0, 0.95)
        cx.set_line_width(1.5)
        cx.rectangle(rx, ry, rw, rh)
        cx.stroke()
