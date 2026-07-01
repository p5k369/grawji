"""Custom recipe controls: a labelled slider row and the Fuji WB grid."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk
from rawji.fuji_enums import FP_WB_SHIFT_MAX

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

        self._adj = Gtk.Adjustment(
            lower=lower, upper=upper, step_increment=step, page_increment=step
        )
        self._scale = Gtk.Scale(
            adjustment=self._adj,
            hexpand=False,
            draw_value=False,
            valign=Gtk.Align.CENTER,
        )
        self._scale.set_size_request(147, -1)
        self._label = Gtk.Label(xalign=1.0)
        self._label.add_css_class("dim-label")
        self._value_chars = max(len(self._fmt(lower)), len(self._fmt(upper)))
        self._label.set_width_chars(self._value_chars)

        suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        suffix.set_valign(Gtk.Align.CENTER)
        suffix.append(self._scale)
        suffix.append(self._label)
        self.add_suffix(suffix)
        self._adj.connect("value-changed", self._on_value_changed)
        self._update_label()

    def _on_value_changed(self, _adj: Gtk.Adjustment) -> None:
        """Refresh the label with the snapped value."""
        self._update_label()

    def _update_label(self) -> None:
        """Refresh the trailing value label with the snapped value."""
        self._label.set_text(self._fmt(self.get_value()))

    def get_value(self) -> float:
        """Return the current (snapped) slider value."""
        return round(self._adj.get_value() / self._step) * self._step

    def set_value(self, value: float) -> None:
        """Set the slider value (snapped)."""
        self._adj.set_value(round(value / self._step) * self._step)

    def connect_changed(self, callback: Callable[..., None]) -> None:
        """Call callback whenever the value changes."""
        self._adj.connect("value-changed", callback)

    def set_range(self, lower: float, upper: float) -> None:
        """Change the value bounds (value clamps into the new range)."""
        self._adj.set_lower(lower)
        self._adj.set_upper(upper)
        self._update_label()

    @property
    def value_chars(self) -> int:
        """Character width its own value text needs."""
        return self._value_chars

    def set_value_chars(self, chars: int) -> None:
        """Override the value column width so rows can share one width."""
        self._label.set_width_chars(chars)


class WBShiftGrid(Gtk.DrawingArea):
    """The classic Fujifilm white-balance shift grid.

    A square grid spanning -9 to +9 on both axes: the horizontal axis is
    the red shift (right = +R) and the vertical axis is the blue shift
    (up = +B), matching the in-camera WB-SHIFT screen. Click or drag to
    place the marker; values snap to integers.
    """

    _RANGE = FP_WB_SHIFT_MAX

    def __init__(self) -> None:
        """Create the grid widget."""
        super().__init__()
        self._r = 0
        self._b = 0
        self._colored = False
        self._on_changed: Callable[[int, int], None] | None = None
        self.set_content_width(164)
        self.set_content_height(164)
        self.set_draw_func(self._draw)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self._on_drag)
        self.add_controller(drag)

    def set_colored(self, colored: bool) -> None:
        """Tint each cell to preview its white-balance shift, or not."""
        self._colored = colored
        self.queue_draw()

    @staticmethod
    def _cell_rgb(r: float, b: float) -> tuple[float, float, float]:
        """Opponent-colour tint for a shift: +R red, +B blue, and inverses."""
        base, amp = 0.5, 0.22
        red = base + amp * (r - b)
        green = base - amp * (r + b)
        blue = base + amp * (b - r)
        clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
        return clamp(red), clamp(green), clamp(blue)

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

    def _geometry(self) -> tuple[float, float, float, float]:
        """Return (origin_x, origin_y, size, step) of the square plot area.

        The square is anchored near the top (only a small bottom margin) so
        a readout placed directly beneath the widget sits close to it.
        """
        margin = 12.0
        bottom = 3.0
        size = min(
            self.get_width() - 2 * margin,
            self.get_height() - margin - bottom,
        )
        size = max(size, 1.0)
        ox = (self.get_width() - size) / 2
        return ox, margin, size, size / (2 * self._RANGE)

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
        ox, oy, size, _ = self._geometry()
        frac_x = (x - ox) / size
        frac_y = (y - oy) / size
        red = round((frac_x * 2 - 1) * self._RANGE)
        blue = round((1 - frac_y * 2) * self._RANGE)
        red = max(-self._RANGE, min(self._RANGE, red))
        blue = max(-self._RANGE, min(self._RANGE, blue))
        if (red, blue) != (self._r, self._b):
            self._r, self._b = red, blue
            self.queue_draw()
            if self._on_changed is not None:
                self._on_changed(self._r, self._b)

    def _fill_cells(self, cx: Any, ox: float, oy: float, step: float) -> None:
        """Tint every grid cell with its opponent-colour shift preview."""
        cells = 2 * self._RANGE
        for col in range(cells):
            r = ((col + 0.5) / cells) * 2 - 1
            for row in range(cells):
                b = 1 - ((row + 0.5) / cells) * 2
                red, green, blue = self._cell_rgb(r, b)
                cx.set_source_rgb(red, green, blue)
                cx.rectangle(ox + col * step, oy + row * step, step, step)
                cx.fill()

    def _draw(
        self, _area: Gtk.DrawingArea, cx: Any, _width: int, _height: int
    ) -> None:
        """Draw the grid, axes and current marker (cx is a cairo ctx)."""
        ox, oy, size, step = self._geometry()
        color = self.get_color()

        if self._colored:
            self._fill_cells(cx, ox, oy, step)

        # Grid lines and axes stay bright regardless of the light/dark theme
        cx.set_line_width(1.0)
        cx.set_source_rgba(1.0, 1.0, 1.0, 0.2)
        for i in range(2 * self._RANGE + 1):
            pos = i * step
            cx.move_to(ox, oy + pos)
            cx.line_to(ox + size, oy + pos)
            cx.move_to(ox + pos, oy)
            cx.line_to(ox + pos, oy + size)
        cx.stroke()

        mid_x = ox + size / 2
        mid_y = oy + size / 2
        cx.set_source_rgba(1.0, 1.0, 1.0, 0.45)
        cx.set_line_width(1.5)
        cx.move_to(ox, mid_y)
        cx.line_to(ox + size, mid_y)
        cx.move_to(mid_x, oy)
        cx.line_to(mid_x, oy + size)
        cx.stroke()

        px = ox + (self._r / self._RANGE / 2 + 0.5) * size
        py = oy + (0.5 - self._b / self._RANGE / 2) * size
        if self._colored:
            cx.set_source_rgb(1.0, 1.0, 1.0)
            cx.arc(px, py, 5.5, 0, 6.2832)
            cx.fill()
            cx.set_source_rgb(0.0, 0.0, 0.0)
            cx.arc(px, py, 3.5, 0, 6.2832)
            cx.fill()
        else:
            cx.set_source_rgba(color.red, color.green, color.blue, 0.95)
            cx.arc(px, py, 5.0, 0, 6.2832)
            cx.fill()
