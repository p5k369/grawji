"""Custom recipe controls: a labelled slider row and the Fuji WB grid."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

Formatter = Callable[[float], str]


class SliderRow(Adw.ActionRow):
    """An Adw.ActionRow with a snapping slider and a value label.

    The slider snaps to multiples of step and the trailing label shows
    the value via fmt. connect_changed wires a callback to user (or
    programmatic) value changes, matching the window's recipe-signal flow.
    """

    def __init__(
        self,
        title: str,
        *,
        lower: float,
        upper: float,
        step: float = 1.0,
        fmt: Formatter | None = None,
    ) -> None:
        """Create the row.

        Args:
            title: Row title shown on the left.
            lower: Minimum value.
            upper: Maximum value.
            step: Snap increment (slider value rounds to a multiple).
            fmt: Value -> label formatter; defaults to a signed integer.
        """
        super().__init__(title=title)
        self._step = step
        self._fmt: Formatter = fmt or (lambda v: f"{round(v):+d}")
        self._snapping = False

        self._adj = Gtk.Adjustment(
            lower=lower, upper=upper, step_increment=step, page_increment=step
        )
        self._scale = Gtk.Scale(
            adjustment=self._adj,
            hexpand=True,
            draw_value=False,
            valign=Gtk.Align.CENTER,
        )
        self._scale.set_size_request(150, -1)
        self._label = Gtk.Label(xalign=1.0)
        self._label.add_css_class("dim-label")
        self._label.set_width_chars(7)

        self.add_suffix(self._scale)
        self.add_suffix(self._label)
        self._adj.connect("value-changed", self._on_value_changed)
        self._update_label()

    def _on_value_changed(self, _adj: Gtk.Adjustment) -> None:
        """Snap the value to the step grid and refresh the label."""
        if self._snapping:
            return
        raw = self._adj.get_value()
        snapped = round(raw / self._step) * self._step
        if abs(snapped - raw) > 1e-9:
            self._snapping = True
            self._adj.set_value(snapped)
            self._snapping = False
        self._update_label()

    def _update_label(self) -> None:
        """Refresh the trailing value label."""
        self._label.set_text(self._fmt(self._adj.get_value()))

    def get_value(self) -> float:
        """Return the current (snapped) slider value."""
        return round(self._adj.get_value() / self._step) * self._step

    def set_value(self, value: float) -> None:
        """Set the slider value (snapped)."""
        self._adj.set_value(round(value / self._step) * self._step)

    def connect_changed(self, callback: Callable[..., None]) -> None:
        """Call callback whenever the value changes."""
        self._adj.connect("value-changed", callback)


class WBShiftGrid(Gtk.DrawingArea):
    """The classic Fujifilm white-balance shift grid.

    A square grid spanning -9 to +9 on both axes: the horizontal axis is
    the red shift (right = +R) and the vertical axis is the blue shift
    (up = +B), matching the in-camera WB-SHIFT screen. Click or drag to
    place the marker; values snap to integers.
    """

    _RANGE = 9

    def __init__(self) -> None:
        """Create the grid widget."""
        super().__init__()
        self._r = 0
        self._b = 0
        self._on_changed: Callable[[int, int], None] | None = None
        self.set_content_width(176)
        self.set_content_height(176)
        self.set_draw_func(self._draw)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self._on_drag)
        self.add_controller(drag)

    def get_values(self) -> tuple[int, int]:
        """Return the current (red, blue) shift."""
        return self._r, self._b

    def set_values(self, red: int, blue: int) -> None:
        """Set the marker position without invoking the change callback."""
        self._r = max(-self._RANGE, min(self._RANGE, int(red)))
        self._b = max(-self._RANGE, min(self._RANGE, int(blue)))
        self.queue_draw()

    def connect_changed(self, callback: Callable[[int, int], None]) -> None:
        """Register a callback invoked with (red, blue) on user input."""
        self._on_changed = callback

    def _geometry(self) -> tuple[float, float, float]:
        """Return (origin, size, step) of the square plot area."""
        margin = 18.0
        size = min(self.get_width(), self.get_height()) - 2 * margin
        size = max(size, 1.0)
        return margin, size, size / (2 * self._RANGE)

    def _on_pressed(
        self, _g: Gtk.GestureClick, _n: int, x: float, y: float
    ) -> None:
        """Set the marker from a click position."""
        self._set_from_pixel(x, y)

    def _on_drag(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        """Set the marker while dragging."""
        ok, sx, sy = gesture.get_start_point()
        if ok:
            self._set_from_pixel(sx + dx, sy + dy)

    def _set_from_pixel(self, x: float, y: float) -> None:
        """Convert a pixel position to a snapped (R, B) and notify."""
        origin, size, _ = self._geometry()
        frac_x = (x - origin) / size  # 0 to 1 left->right
        frac_y = (y - origin) / size  # 0 to 1 top->bottom
        red = round((frac_x * 2 - 1) * self._RANGE)  # right = +R
        blue = round((1 - frac_y * 2) * self._RANGE)  # up = +B
        red = max(-self._RANGE, min(self._RANGE, red))
        blue = max(-self._RANGE, min(self._RANGE, blue))
        if (red, blue) != (self._r, self._b):
            self._r, self._b = red, blue
            self.queue_draw()
            if self._on_changed is not None:
                self._on_changed(self._r, self._b)

    def _draw(
        self, _area: Gtk.DrawingArea, cx: Any, _width: int, _height: int
    ) -> None:
        """Draw the grid, axes and current marker (cx is a cairo ctx)."""
        origin, size, step = self._geometry()
        color = self.get_color()

        # Grid lines.
        cx.set_line_width(1.0)
        cx.set_source_rgba(color.red, color.green, color.blue, 0.15)
        for i in range(2 * self._RANGE + 1):
            pos = origin + i * step
            cx.move_to(origin, pos)
            cx.line_to(origin + size, pos)
            cx.move_to(pos, origin)
            cx.line_to(pos, origin + size)
        cx.stroke()

        # Centre axes, a little stronger.
        mid = origin + size / 2
        cx.set_source_rgba(color.red, color.green, color.blue, 0.4)
        cx.set_line_width(1.5)
        cx.move_to(origin, mid)
        cx.line_to(origin + size, mid)
        cx.move_to(mid, origin)
        cx.line_to(mid, origin + size)
        cx.stroke()

        # Marker (R horizontal, B vertical).
        px = origin + (self._r / self._RANGE / 2 + 0.5) * size
        py = origin + (0.5 - self._b / self._RANGE / 2) * size
        cx.set_source_rgba(color.red, color.green, color.blue, 0.95)
        cx.arc(px, py, 5.0, 0, 6.2832)
        cx.fill()

        # Axis labels (B top, R right).
        cx.set_source_rgba(color.red, color.green, color.blue, 0.7)
        cx.move_to(mid + 3, origin - 5)
        cx.show_text("B")
        cx.move_to(origin + size + 4, mid + 4)
        cx.show_text("R")
