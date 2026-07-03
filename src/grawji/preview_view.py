"""The preview viewport: zoom, pan, peek, rotation, background, histogram."""

from __future__ import annotations

from importlib import resources
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Gtk

from grawji.widgets import Histogram

# Manual rotation (degrees clockwise) -> GdkPixbuf rotation.
_ROTATIONS = {
    90: GdkPixbuf.PixbufRotation.CLOCKWISE,
    180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
    270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE,
}

# Zoom is multiplicative.
ZOOM_STEP = 1.15

# Preview canvas backgrounds, cycled by the toolbar button (darktable-style).
BACKGROUNDS = ["", "canvas-white", "canvas-gray", "canvas-black"]

_UI = (
    resources.files("grawji")
    .joinpath("ui", "preview_view.ui")
    .read_text(encoding="utf-8")
)


class _ScaledPaintable(GObject.GObject, Gdk.Paintable):
    """A texture presented at a chosen intrinsic size, GPU-scaled on draw."""

    def __init__(self, texture: Any, width: int, height: int) -> None:
        """Present texture as though it were width x height pixels."""
        super().__init__()
        self._texture = texture
        self._width = max(1, width)
        self._height = max(1, height)

    def do_get_intrinsic_width(self) -> int:
        """Report the chosen width to the layout system."""
        return self._width

    def do_get_intrinsic_height(self) -> int:
        """Report the chosen height to the layout system."""
        return self._height

    def do_snapshot(self, snapshot: Any, width: float, height: float) -> None:
        """Draw the texture scaled into the given area."""
        self._texture.snapshot(snapshot, width, height)


def oriented_pixbuf(jpeg: bytes) -> Any:
    """Decode JPEG bytes into an EXIF-oriented pixbuf.

    GTK-free so it can run on a worker thread; the result is only
    attached to widgets back on the main thread.
    """
    loader = GdkPixbuf.PixbufLoader()
    loader.write(jpeg)
    loader.close()
    return loader.get_pixbuf().apply_embedded_orientation()


@Gtk.Template(string=_UI)
class PreviewView(Gtk.Box):
    """The rendered-image viewport plus its status/tool strip.

    Owns everything about presenting a JPEG: zoom (Ctrl+scroll or the
    win.zoom-* actions), drag panning, hold-to-peek at the in-camera
    original, manual rotation, the cycling canvas background and the
    histogram overlay. The window feeds it JPEGs and reads back the
    rotation when exporting.
    """

    __gtype_name__ = "GrawjiPreviewView"

    scroll = Gtk.Template.Child()
    picture = Gtk.Template.Child()
    histogram_slot = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    status = Gtk.Template.Child()
    peek_button = Gtk.Template.Child()
    rotate_left = Gtk.Template.Child()
    rotate_right = Gtk.Template.Child()

    def __init__(self, **kwargs: object) -> None:
        """Wire the zoom, pan, peek and rotation controllers."""
        super().__init__(**kwargs)
        self._zoom = 1.0
        self._rotation = 0
        self._pixbuf: Any | None = None
        self._original_pixbuf: Any | None = None
        self._last_jpeg: bytes | None = None
        self._embedded_jpeg: bytes | None = None
        self._peek = False
        self._pan_h = 0.0
        self._pan_v = 0.0
        self._pointer: tuple[float, float] | None = None
        self._content_w = 0.0
        self._content_h = 0.0
        self._texture: Any = None
        self._texture_src: Any = None
        self._background = ""

        self.rotate_left.connect("clicked", lambda *_a: self.rotate(-90))
        self.rotate_right.connect("clicked", lambda *_a: self.rotate(90))

        self._histogram = Histogram()
        self._histogram.set_hexpand(True)
        self._histogram.set_vexpand(True)
        self.histogram_slot.append(self._histogram)

        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll.connect("scroll", self._on_scroll_zoom)
        self.scroll.add_controller(scroll)
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_pointer_motion)
        motion.connect("leave", self._on_pointer_leave)
        self.scroll.add_controller(motion)
        pan = Gtk.GestureDrag()
        pan.connect("drag-begin", self._on_pan_begin)
        pan.connect("drag-update", self._on_pan_update)
        self.scroll.add_controller(pan)

        peek = Gtk.GestureClick()
        peek.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        peek.connect("pressed", self._on_peek_pressed)
        peek.connect("released", self._on_peek_released)
        peek.connect("cancel", self._on_peek_cancel)
        self.peek_button.add_controller(peek)

    @property
    def rotation(self) -> int:
        """The manual rotation baked into the display, degrees clockwise."""
        return self._rotation

    @property
    def peeking(self) -> bool:
        """Whether the in-camera original is currently shown."""
        return self._peek

    @property
    def background(self) -> str:
        """The current canvas background CSS class ("" for themed)."""
        return self._background

    def set_status(self, text: str) -> None:
        """Set the status-line text."""
        self.status.set_label(text)

    def set_spinner(self, *, active: bool) -> None:
        """Show and run the status-line spinner, or hide and stop it."""
        self.spinner.set_visible(active)
        if active:
            self.spinner.start()
        else:
            self.spinner.stop()

    def set_show_histogram(self, show: bool) -> None:
        """Show or hide the histogram overlay."""
        self.histogram_slot.set_visible(show)

    def set_embedded_jpeg(self, jpeg: bytes | None) -> None:
        """Provide the image's in-camera JPEG, the peek source."""
        self._embedded_jpeg = jpeg

    @property
    def has_embedded_jpeg(self) -> bool:
        """Whether an in-camera JPEG is available for the current image."""
        return self._embedded_jpeg is not None

    def clear_source(self) -> None:
        """Forget the source JPEG (a new selection failed to decode)."""
        self._last_jpeg = None

    def reset_rotation(self) -> None:
        """Clear the manual rotation (for a new selection)."""
        self._rotation = 0

    def show_jpeg(self, jpeg: bytes) -> bool:
        """Display JPEG bytes in the preview; False if undecodable."""
        self._last_jpeg = jpeg
        try:
            pixbuf = self.pixbuf_from_jpeg(jpeg)
        except GLib.Error as exc:
            self.set_status(f"Cannot display image: {exc}")
            return False
        self.show_pixbuf(pixbuf)
        return True

    def show_pixbuf(self, pixbuf: Any, *, jpeg: bytes | None = None) -> None:
        """Display an already-decoded pixbuf (jpeg is its source bytes)."""
        if jpeg is not None:
            self._last_jpeg = jpeg
        self._pixbuf = pixbuf
        self._original_pixbuf = None
        self._peek = False
        self.peek_button.set_sensitive(True)
        self.rotate_left.set_sensitive(True)
        self.rotate_right.set_sensitive(True)
        self._histogram.update(pixbuf)
        self._apply_zoom()

    def pixbuf_from_jpeg(self, jpeg: bytes) -> Any:
        """Decode JPEG bytes, applying EXIF orientation and rotation."""
        pixbuf = oriented_pixbuf(jpeg)
        rotation = _ROTATIONS.get(self._rotation)
        return pixbuf.rotate_simple(rotation) if rotation else pixbuf

    def rotate(self, degrees: int) -> None:
        """Update the rotation and redisplay the current image."""
        self._rotation = (self._rotation + degrees) % 360
        if self._last_jpeg is not None:
            self.show_jpeg(self._last_jpeg)

    def zoom_in(self) -> None:
        """Zoom in one step, centred on the viewport."""
        self.set_zoom(self._zoom * ZOOM_STEP)

    def zoom_out(self) -> None:
        """Zoom out one step, centred on the viewport."""
        self.set_zoom(self._zoom / ZOOM_STEP)

    def zoom_fit(self) -> None:
        """Reset the zoom so the image fits the viewport."""
        self.set_zoom(1.0)

    def set_zoom(
        self, value: float, anchor: tuple[float, float] | None = None
    ) -> None:
        """Set the preview zoom, keeping anchor fixed under the pointer."""
        value = max(0.1, min(value, 8.0))
        if value == self._zoom:
            return
        hadj = self.scroll.get_hadjustment()
        vadj = self.scroll.get_vadjustment()
        vw = self.scroll.get_width()
        vh = self.scroll.get_height()
        ax, ay = anchor if anchor is not None else (vw / 2, vh / 2)
        fx = self._content_fraction(hadj, ax)
        fy = self._content_fraction(vadj, ay)

        self._zoom = value
        self._apply_zoom()
        self._anchor_scroll(hadj, fx, ax, self._content_w, vw)
        self._anchor_scroll(vadj, fy, ay, self._content_h, vh)

    def set_peek(self, *, peeking: bool) -> None:
        """Show the in-camera original while peeking, else the result."""
        if peeking and self._original_pixbuf is None:
            if self._embedded_jpeg is None:
                return
            try:
                self._original_pixbuf = self.pixbuf_from_jpeg(
                    self._embedded_jpeg
                )
            except GLib.Error:
                return
        self._peek = peeking
        self._apply_zoom()

    def set_background(self, css_class: str) -> None:
        """Set the preview canvas background to the given CSS class."""
        for cls in BACKGROUNDS:
            if cls:
                self.scroll.remove_css_class(cls)
        if css_class:
            self.scroll.add_css_class(css_class)
        self._background = css_class

    def cycle_background(self) -> str:
        """Advance to the next canvas background; returns its CSS class."""
        index = (
            BACKGROUNDS.index(self._background)
            if self._background in BACKGROUNDS
            else 0
        )
        self.set_background(BACKGROUNDS[(index + 1) % len(BACKGROUNDS)])
        return self._background

    def _on_peek_pressed(
        self, gesture: Gtk.GestureClick, _n: int, _x: float, _y: float
    ) -> None:
        """Start peeking; claim the press so the button does not cancel it."""
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.set_peek(peeking=True)

    def _on_peek_released(
        self, _gesture: Gtk.GestureClick, _n: int, _x: float, _y: float
    ) -> None:
        """Stop peeking when the button is released."""
        self.set_peek(peeking=False)

    def _on_peek_cancel(self, _gesture: Gtk.GestureClick, _seq: Any) -> None:
        """Stop peeking if the gesture is cancelled (e.g. pointer lost)."""
        self.set_peek(peeking=False)

    def _on_pan_begin(self, _gesture: Any, _x: float, _y: float) -> None:
        """Remember the scroll position at the start of a pan drag."""
        self._pan_h = self.scroll.get_hadjustment().get_value()
        self._pan_v = self.scroll.get_vadjustment().get_value()

    def _on_pan_update(self, _gesture: Any, dx: float, dy: float) -> None:
        """Pan the zoomed preview by dragging."""
        self.scroll.get_hadjustment().set_value(self._pan_h - dx)
        self.scroll.get_vadjustment().set_value(self._pan_v - dy)

    def _on_pointer_motion(self, _c: Any, x: float, y: float) -> None:
        """Remember the pointer position within the preview viewport."""
        self._pointer = (x, y)

    def _on_pointer_leave(self, _c: Any) -> None:
        """Forget the pointer when it leaves the preview."""
        self._pointer = None

    def _on_scroll_zoom(self, controller: Any, _dx: float, dy: float) -> bool:
        """Zoom the preview on Ctrl+scroll."""
        event = controller.get_current_event()
        state = event.get_modifier_state() if event else 0
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        factor = ZOOM_STEP if dy < 0 else 1 / ZOOM_STEP
        self.set_zoom(self._zoom * factor, anchor=self._pointer)
        return True

    @staticmethod
    def _content_fraction(adj: Any, anchor: float) -> float:
        """Fraction of the content that currently sits under anchor."""
        upper = adj.get_upper()
        if upper <= 0:
            return 0.5
        return (adj.get_value() + anchor) / upper

    @staticmethod
    def _anchor_scroll(
        adj: Any, frac: float, anchor: float, content: float, viewport: float
    ) -> None:
        """Place content fraction frac under anchor, for the new extent."""
        adj.set_upper(max(content, viewport))
        target = frac * content - anchor
        adj.set_value(max(0.0, min(target, max(0.0, content - viewport))))

    def _preview_pixbuf(self) -> Any:
        """The pixbuf to show: the original while peeking, else the result."""
        if self._peek and self._original_pixbuf is not None:
            return self._original_pixbuf
        return self._pixbuf

    def _apply_zoom(self) -> None:
        """Show the preview at the current zoom factor."""
        pixbuf = self._preview_pixbuf()
        if pixbuf is None:
            return
        pw, ph = pixbuf.get_width(), pixbuf.get_height()
        if pw <= 0 or ph <= 0:
            return
        if self._texture_src is not pixbuf:
            self._texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            self._texture_src = pixbuf
        vw = self.scroll.get_width() or pw
        vh = self.scroll.get_height() or ph
        if self._zoom == 1.0:
            self.picture.set_can_shrink(True)
            self.picture.set_halign(Gtk.Align.FILL)
            self.picture.set_valign(Gtk.Align.FILL)
            self.picture.set_paintable(self._texture)
            self._content_w, self._content_h = vw, vh
            return
        fit = min(vw / pw, vh / ph)
        sw = max(1, int(pw * fit * self._zoom))
        sh = max(1, int(ph * fit * self._zoom))
        self.picture.set_can_shrink(False)
        self.picture.set_halign(Gtk.Align.CENTER)
        self.picture.set_valign(Gtk.Align.CENTER)
        self.picture.set_paintable(_ScaledPaintable(self._texture, sw, sh))
        self._content_w, self._content_h = sw, sh
