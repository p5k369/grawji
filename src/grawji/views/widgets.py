"""Custom recipe controls: a labelled slider row and the Fuji WB grid."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from functools import partial
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GdkPixbuf, GLib, Gtk
from rawji.fuji_enums import FP_WB_SHIFT_MAX

Formatter = Callable[[float], str]
Dispatch = Callable[[Callable[[], None]], Any]


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

        self._suffix = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=2
        )
        self._suffix.set_valign(Gtk.Align.CENTER)
        self._suffix.append(self._scale)
        self._suffix.append(self._label)
        self.add_suffix(self._suffix)
        self._entry: Gtk.Entry | None = None
        self._adj.connect("value-changed", self._on_value_changed)
        self._update_label()

    def _on_value_changed(self, _adj: Gtk.Adjustment) -> None:
        """Refresh the label with the snapped value."""
        self._update_label()

    def _update_label(self) -> None:
        """Refresh the trailing value label (and the entry, if editable)."""
        text = self._fmt(self.get_value())
        self._label.set_text(text)
        if self._entry is not None and not self._entry.has_focus():
            self._entry.set_text(text)

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

    def set_step(self, step: float, fmt: Formatter | None = None) -> None:
        """Change the snap increment (and optionally the formatter).

        The current value re-snaps to the new increment. Used to widen
        or narrow a row's granularity per camera body, e.g. half-step
        tone on XProcessor5 bodies.
        """
        self._step = step
        self._adj.set_step_increment(step)
        self._adj.set_page_increment(step)
        if fmt is not None:
            self._fmt = fmt
        self.set_value(self._adj.get_value())
        self._update_label()

    @property
    def value_chars(self) -> int:
        """Character width its own value text needs."""
        return self._value_chars

    def set_value_chars(self, chars: int) -> None:
        """Override the value column width so rows can share one width."""
        self._label.set_width_chars(chars)

    def set_editable(self, editable: bool) -> None:
        """Swap the read-only value label for a typeable entry."""
        if editable and self._entry is None:
            self._entry = Gtk.Entry(
                xalign=1.0,
                width_chars=self._value_chars,
                max_width_chars=self._value_chars,
                input_purpose=Gtk.InputPurpose.DIGITS,
            )
            self._entry.connect("activate", lambda *_a: self._apply_entry())
            focus = Gtk.EventControllerFocus()
            focus.connect("leave", lambda *_a: self._apply_entry())
            self._entry.add_controller(focus)
            self._suffix.append(self._entry)
        if self._entry is not None:
            self._entry.set_visible(editable)
        self._label.set_visible(not editable)
        self._update_label()

    def _apply_entry(self) -> None:
        """Parse the entry text (digits only) and set the snapped value."""
        if self._entry is None:
            return
        digits = "".join(c for c in self._entry.get_text() if c.isdigit())
        if digits:
            self.set_value(float(digits))
        # Show the snapped/clamped result even while the entry is focused.
        self._entry.set_text(self._fmt(self.get_value()))


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
        self._range = self._RANGE
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

    def set_range(self, half: int) -> None:
        """Set the +/- range each axis spans (re-clamps and redraws)."""
        self._range = max(1, int(half))
        self.set_values(self._r, self._b)
        self.queue_draw()

    def _cell_rgb(self, r: float, b: float) -> tuple[float, float, float]:
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
        self._r = max(-self._range, min(self._range, int(red)))
        self._b = max(-self._range, min(self._range, int(blue)))
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
        return ox, margin, size, size / (2 * self._range)

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
        red = round((frac_x * 2 - 1) * self._range)
        blue = round((1 - frac_y * 2) * self._range)
        red = max(-self._range, min(self._range, red))
        blue = max(-self._range, min(self._range, blue))
        if (red, blue) != (self._r, self._b):
            self._r, self._b = red, blue
            self.queue_draw()
            if self._on_changed is not None:
                self._on_changed(self._r, self._b)

    def _fill_cells(self, cx: Any, ox: float, oy: float, step: float) -> None:
        """Tint every grid cell with its opponent-colour shift preview."""
        cells = 2 * self._range
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

        if self._colored:
            line = (1.0, 1.0, 1.0)
        else:
            line = (color.red, color.green, color.blue)
        cx.set_line_width(1.0)
        cx.set_source_rgba(*line, 0.2)
        for i in range(2 * self._range + 1):
            pos = i * step
            cx.move_to(ox, oy + pos)
            cx.line_to(ox + size, oy + pos)
            cx.move_to(ox + pos, oy)
            cx.line_to(ox + pos, oy + size)
        cx.stroke()

        mid_x = ox + size / 2
        mid_y = oy + size / 2
        cx.set_source_rgba(*line, 0.45)
        cx.set_line_width(1.5)
        cx.move_to(ox, mid_y)
        cx.line_to(ox + size, mid_y)
        cx.move_to(mid_x, oy)
        cx.line_to(mid_x, oy + size)
        cx.stroke()

        px = ox + (self._r / self._range / 2 + 0.5) * size
        py = oy + (0.5 - self._b / self._range / 2) * size
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


class MonoColorGrid(WBShiftGrid):
    """The Monochromatic Color toning grid for black-and-white sims.

    Horizontal axis is magenta-green (right = green), vertical is warm-cool
    (up = warm, so cool/minus is down), matching the in-camera grid. The x
    value is magenta-green and y is warm-cool. Same click/drag/snap grid as
    the WB shift, with a warm-cool / magenta-green tint and its own range.
    """

    _RANGE = 18

    def _cell_rgb(self, x: float, y: float) -> tuple[float, float, float]:
        """Tint a cell: x green(+)/magenta(-), y warm(+)/cool(-)."""
        base, amp = 0.5, 0.22
        red = base + amp * (y * 0.7 - x)
        green = base + amp * x
        blue = base + amp * (-y * 0.7 - x)
        clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
        return clamp(red), clamp(green), clamp(blue)


class Histogram(Gtk.DrawingArea):
    """A compact histogram overlaid on the preview.

    Click to switch between RGB (per-channel colour) and Luminance (the
    shadow-to-highlight tonal distribution). Binning runs off the main
    thread on a downscaled copy; a generation token drops stale results.
    """

    _SAMPLE = 160
    _RADIUS = 8.0
    # RGB channel fill colours.
    _RGB_COLOURS = ((0.9, 0.32, 0.32), (0.34, 0.85, 0.4), (0.4, 0.55, 1.0))
    _LUMA_COLOUR = (0.85, 0.85, 0.85)
    # A channel is "clipped" once this fraction of pixels sits at 0 or 255.
    _CLIP_FRACTION = 0.002
    _CLIP_SIZE = 11.0

    def __init__(self, *, dispatch: Dispatch = GLib.idle_add) -> None:
        """Create the histogram; dispatch schedules a redraw on the UI loop."""
        super().__init__()
        self._dispatch = dispatch
        # (red, green, blue, luma) each a 256-bin count list, or None.
        self._bins: tuple[list[int], ...] | None = None
        self._generation = 0
        self._luma = False
        self.set_tooltip_text("Click to switch RGB / Luminance")
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        click.connect("released", self._on_clicked)
        self.add_controller(click)

    def _on_clicked(self, *_args: Any) -> None:
        """Toggle between RGB and Luminance views."""
        self._luma = not self._luma
        self.queue_draw()

    def update(self, pixbuf: Any) -> None:
        """Recompute the histogram for pixbuf (or clear it if None)."""
        self._generation += 1
        if pixbuf is None:
            self._bins = None
            self.queue_draw()
            return
        small = self._downscale(pixbuf)
        threading.Thread(
            target=self._bin,
            args=(
                bytes(small.get_pixels()),
                small.get_n_channels(),
                small.get_rowstride(),
                small.get_width(),
                small.get_height(),
                self._generation,
            ),
            name="grawji-histogram",
            daemon=True,
        ).start()

    @classmethod
    def _downscale(cls, pixbuf: Any) -> Any:
        """Scale pixbuf down so its longest edge is at most _SAMPLE."""
        width, height = pixbuf.get_width(), pixbuf.get_height()
        scale = min(1.0, cls._SAMPLE / max(width, height, 1))
        return pixbuf.scale_simple(
            max(1, round(width * scale)),
            max(1, round(height * scale)),
            GdkPixbuf.InterpType.BILINEAR,
        )

    def _bin(
        self,
        data: bytes,
        channels: int,
        stride: int,
        width: int,
        height: int,
        generation: int,
    ) -> None:
        """Count per-channel and luma levels off-thread, then dispatch."""
        red, green, blue, luma = ([0] * 256 for _ in range(4))
        for y in range(height):
            base = y * stride
            for x in range(width):
                i = base + x * channels
                r, g, b = data[i], data[i + 1], data[i + 2]
                red[r] += 1
                green[g] += 1
                blue[b] += 1
                # Rec. 601 luma - good enough for a tonal readout.
                luma[(r * 299 + g * 587 + b * 114) // 1000] += 1
        self._dispatch(
            partial(self._store, (red, green, blue, luma), generation)
        )

    def _store(self, bins: tuple[list[int], ...], generation: int) -> None:
        """Adopt fresh bins unless a newer update has superseded them."""
        if generation == self._generation:
            self._bins = bins
            self.queue_draw()

    def _draw(
        self, _area: Gtk.DrawingArea, cx: Any, width: int, height: int
    ) -> None:
        """Draw a rounded dark panel and the current channels (cx is cairo)."""
        self._rounded_rect(cx, width, height, self._RADIUS)
        cx.set_source_rgba(0.0, 0.0, 0.0, 0.55)
        cx.fill_preserve()
        cx.clip()
        if self._bins is None:
            return
        red, green, blue, luma = self._bins
        if self._luma:
            channels = [(luma, self._LUMA_COLOUR)]
        else:
            channels = list(
                zip((red, green, blue), self._RGB_COLOURS, strict=True)
            )
        peak = max(1, *(max(chan[1:255]) for chan, _ in channels))
        for chan, (cr, cg, cb) in channels:
            cx.set_source_rgba(cr, cg, cb, 0.6)
            cx.move_to(0, height)
            for level in range(256):
                value = min(chan[level], peak)
                cx.line_to(level / 255 * width, height - value / peak * height)
            cx.line_to(width, height)
            cx.close_path()
            cx.fill()
        self._draw_clipping(cx, width, red, green, blue, luma)

    def _draw_clipping(
        self,
        cx: Any,
        width: int,
        red: list[int],
        green: list[int],
        blue: list[int],
        luma: list[int],
    ) -> None:
        """Light corner triangles for shadow/highlight clipping."""
        total = max(1, sum(red))
        if self._luma:
            shadow = self._LUMA_COLOUR if self._clips(luma[0], total) else None
            highlight = (
                self._LUMA_COLOUR if self._clips(luma[255], total) else None
            )
        else:
            shadow = self._rgb_clip(red[0], green[0], blue[0], total)
            highlight = self._rgb_clip(red[255], green[255], blue[255], total)
        if shadow is not None:
            self._corner(cx, width, shadow, left=True)
        if highlight is not None:
            self._corner(cx, width, highlight, left=False)

    def _clips(self, count: int, total: int) -> bool:
        """Return True if count is a clipping-worthy share of total."""
        return count / total > self._CLIP_FRACTION

    def _rgb_clip(
        self, r: int, g: int, b: int, total: int
    ) -> tuple[float, float, float] | None:
        """Combine per-channel clipping into one marker colour, or None."""
        colour = (
            float(self._clips(r, total)),
            float(self._clips(g, total)),
            float(self._clips(b, total)),
        )
        return colour if any(colour) else None

    def _corner(
        self,
        cx: Any,
        width: int,
        colour: tuple[float, float, float],
        *,
        left: bool,
    ) -> None:
        """Fill a small right-triangle in a top corner."""
        s = self._CLIP_SIZE
        cx.set_source_rgb(*colour)
        if left:
            cx.move_to(0, 0)
            cx.line_to(s, 0)
            cx.line_to(0, s)
        else:
            cx.move_to(width, 0)
            cx.line_to(width - s, 0)
            cx.line_to(width, s)
        cx.close_path()
        cx.fill()

    @staticmethod
    def _rounded_rect(cx: Any, width: int, height: int, r: float) -> None:
        """Add a rounded-rectangle path covering the whole widget."""
        cx.new_sub_path()
        cx.arc(width - r, r, r, -math.pi / 2, 0)
        cx.arc(width - r, height - r, r, 0, math.pi / 2)
        cx.arc(r, height - r, r, math.pi / 2, math.pi)
        cx.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cx.close_path()
