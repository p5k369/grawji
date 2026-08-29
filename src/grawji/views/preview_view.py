"""The preview viewport: zoom, pan, peek, crop, background, histogram."""

from __future__ import annotations

import math
from dataclasses import replace
from importlib import resources
from typing import Any, ClassVar

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import (
    Gdk,
    GdkPixbuf,
    GLib,
    GObject,
    Graphene,
    Gtk,
)

from grawji import crop
from grawji.views.crop_render import bake_pixbuf, orient_pixbuf
from grawji.views.widgets import Histogram

_ZONE_CURSORS = {
    "nw": "nwse-resize",
    "se": "nwse-resize",
    "ne": "nesw-resize",
    "sw": "nesw-resize",
    "n": "ns-resize",
    "s": "ns-resize",
    "e": "ew-resize",
    "w": "ew-resize",
    "move": "move",
}

# Handle grab distance on the crop overlay, in pixels.
_GRAB_PX = 12.0

# A shorter right-button drag than this does not define a horizon.
_MIN_HORIZON_PX = 4.0

# Longest edge of the image used while crop-editing.
_EDIT_MAX_EDGE = 2560

# Zoom is multiplicative.
ZOOM_STEP = 1.15
# Zoom ceiling relative to the image's native pixels (800%)
MAX_NATIVE_ZOOM = 8.0

# Preview canvas backgrounds, cycled by the toolbar button (darktable-style).
BACKGROUNDS = ["", "canvas-white", "canvas-gray", "canvas-black"]

_UI = (
    resources.files("grawji")
    .joinpath("ui", "preview_view.ui")
    .read_text(encoding="utf-8")
)


class _ScaledPaintable(GObject.GObject, Gdk.Paintable):
    """A paintable presented at a chosen intrinsic size, scaled on draw."""

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


class _SplitPaintable(GObject.GObject, Gdk.Paintable):
    """Two textures split by a movable vertical divider."""

    def __init__(self, base: Any, work: Any, width: int, height: int) -> None:
        """Compose base (left) and work (right) at width x height."""
        super().__init__()
        self._base = base
        self._work = work
        self._width = max(1, width)
        self._height = max(1, height)
        self._fraction = 0.5

    def set_fraction(self, fraction: float) -> None:
        """Move the divider to fraction (0 = all working, 1 = all base)."""
        self._fraction = max(0.0, min(1.0, fraction))
        self.invalidate_contents()

    def do_get_intrinsic_width(self) -> int:
        """Report the image width to the layout system."""
        return self._width

    def do_get_intrinsic_height(self) -> int:
        """Report the image height to the layout system."""
        return self._height

    def do_snapshot(self, snapshot: Any, width: float, height: float) -> None:
        """Draw baseline, then working clipped right of the divider."""
        self._base.snapshot(snapshot, width, height)
        split = self._fraction * width
        if split < width:
            snapshot.push_clip(
                Graphene.Rect().init(split, 0, width - split, height)
            )
            self._work.snapshot(snapshot, width, height)
            snapshot.pop()
        white = Gdk.RGBA()
        white.red = white.green = white.blue = white.alpha = 1.0
        shadow = Gdk.RGBA()
        shadow.alpha = 0.35
        # The seam line, plus a grip at mid-height that reads as draggable.
        snapshot.append_color(
            shadow, Graphene.Rect().init(split - 1.5, 0, 3.0, height)
        )
        snapshot.append_color(
            white, Graphene.Rect().init(split - 0.5, 0, 1.0, height)
        )
        grip_h = 44.0
        top = (height - grip_h) / 2
        snapshot.append_color(
            shadow, Graphene.Rect().init(split - 5.0, top, 10.0, grip_h)
        )
        snapshot.append_color(
            white, Graphene.Rect().init(split - 3.0, top, 6.0, grip_h)
        )


def _capped_copy(pixbuf: Any) -> Any:
    """A copy of pixbuf whose longest edge is at most _EDIT_MAX_EDGE."""
    w, h = pixbuf.get_width(), pixbuf.get_height()
    edge = max(w, h)
    if edge <= _EDIT_MAX_EDGE:
        return pixbuf
    scale = _EDIT_MAX_EDGE / edge
    return pixbuf.scale_simple(
        max(1, round(w * scale)),
        max(1, round(h * scale)),
        GdkPixbuf.InterpType.BILINEAR,
    )


class _RotatedPaintable(GObject.GObject, Gdk.Paintable):
    """A texture drawn fine-rotated, sized as the rotation's bounding box."""

    def __init__(self, texture: Any, width: int, height: int) -> None:
        """Wrap texture (width x height pixels), initially unrotated."""
        super().__init__()
        self._texture = texture
        self._width = max(1, width)
        self._height = max(1, height)
        self._angle = 0.0

    def set_angle(self, angle: float) -> None:
        """Set the rotation in degrees clockwise and redraw."""
        if angle == self._angle:
            return
        self._angle = angle
        self.invalidate_size()
        self.invalidate_contents()

    def _bbox(self) -> tuple[float, float]:
        """The rotated bounding box of the texture, in texture pixels."""
        return crop.rotated_size(self._width, self._height, self._angle)

    def do_get_intrinsic_width(self) -> int:
        """Report the bounding-box width to the layout system."""
        return max(1, round(self._bbox()[0]))

    def do_get_intrinsic_height(self) -> int:
        """Report the bounding-box height to the layout system."""
        return max(1, round(self._bbox()[1]))

    def do_snapshot(self, snapshot: Any, width: float, height: float) -> None:
        """Draw the texture rotated about the center of the given area."""
        bw, bh = self._bbox()
        if bw <= 0 or bh <= 0:
            return
        scale = width / bw
        dw, dh = self._width * scale, self._height * scale
        snapshot.save()
        snapshot.translate(Graphene.Point().init(width / 2, height / 2))
        snapshot.rotate(self._angle)
        snapshot.translate(Graphene.Point().init(-dw / 2, -dh / 2))
        self._texture.snapshot(snapshot, dw, dh)
        snapshot.restore()


def oriented_pixbuf(jpeg: bytes) -> Any:
    """Decode jpeg bytes into an exif-oriented pixbuf."""
    loader = GdkPixbuf.PixbufLoader()
    loader.write(jpeg)
    loader.close()
    return loader.get_pixbuf().apply_embedded_orientation()


@Gtk.Template(string=_UI)
class PreviewView(Gtk.Box):
    """The rendered-image viewport plus its status/tool strip.

    Owns everything about presenting a jpeg: zoom (Ctrl+scroll or the
    win.zoom-* actions), drag panning, hold-to-peek at the in-camera
    original, crop and straighten, the cycling canvas background and the
    histogram overlay. The window feeds it jpegs and reads back the
    geometry when exporting.
    """

    __gtype_name__ = "GrawjiPreviewView"

    __gsignals__: ClassVar[dict[str, Any]] = {
        "geometry-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    scroll = Gtk.Template.Child()
    picture = Gtk.Template.Child()
    histogram_slot = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    status = Gtk.Template.Child()
    zoom_label = Gtk.Template.Child()
    peek_button = Gtk.Template.Child()
    rotate_left = Gtk.Template.Child()
    rotate_right = Gtk.Template.Child()
    crop_button = Gtk.Template.Child()
    crop_overlay = Gtk.Template.Child()
    crop_bar = Gtk.Template.Child()
    crop_aspect = Gtk.Template.Child()
    crop_swap = Gtk.Template.Child()
    crop_guides = Gtk.Template.Child()
    guide_flip_h = Gtk.Template.Child()
    guide_flip_v = Gtk.Template.Child()
    angle_scale = Gtk.Template.Child()
    angle_spin = Gtk.Template.Child()
    crop_reset = Gtk.Template.Child()
    crop_cancel = Gtk.Template.Child()
    crop_apply = Gtk.Template.Child()

    def __init__(self, **kwargs: object) -> None:
        """Wire the zoom, pan, peek and crop controllers."""
        super().__init__(**kwargs)
        self._zoom = 1.0
        self._crop = crop.CropRotate()
        self._pre_edit = crop.CropRotate()
        self._editing = False
        self._edit_closing = False
        self._syncing_angle = False
        self._syncing_swap = False
        self._syncing_aspect = False
        self._drag_zone: str | None = None
        self._drag_rect: crop.Rect = crop.FULL_RECT
        self._hover_zone: str | None = None
        self._horizon: tuple[float, float, float, float] | None = None
        self._edit_base: Any | None = None
        self._edit_base_src: Any | None = None
        self._edit_texture: Any = None
        self._edit_texture_size = (0, 0)
        self._edit_orientation = -1
        self._edit_paintable: _RotatedPaintable | None = None
        self._pixbuf: Any | None = None
        self._oriented_pixbuf: Any | None = None
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
        self._compare = False
        self._base_jpeg: bytes | None = None
        self._base_pixbuf: Any | None = None
        self._base_geometry: crop.CropRotate | None = None
        self._split: _SplitPaintable | None = None
        self._split_fraction = 0.5
        self._dragging_divider = False
        self._shown_size: tuple[int, int] | None = None

        self.rotate_left.connect("clicked", lambda *_a: self.rotate(-90))
        self.rotate_right.connect("clicked", lambda *_a: self.rotate(90))
        self._init_crop_controls()

        self._histogram = Histogram()
        self._histogram.set_hexpand(True)
        self._histogram.set_vexpand(True)
        self.histogram_slot.append(self._histogram)
        self._init_viewport_controllers()

    def _init_viewport_controllers(self) -> None:
        """Wire the zoom, pan, pointer and peek controllers."""
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
        pan.connect("drag-end", self._on_pan_end)
        self.scroll.add_controller(pan)

        for adj in (
            self.scroll.get_hadjustment(),
            self.scroll.get_vadjustment(),
        ):
            adj.connect("changed", self._on_viewport_changed)
            adj.connect("value-changed", self._on_viewport_scrolled)

        peek = Gtk.GestureClick()
        peek.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        peek.connect("pressed", self._on_peek_pressed)
        peek.connect("released", self._on_peek_released)
        peek.connect("cancel", self._on_peek_cancel)
        self.peek_button.add_controller(peek)

    def _init_crop_controls(self) -> None:
        """Wire the crop bar, the overlay drawing and its gestures."""
        self.crop_button.connect("toggled", self._on_crop_toggled)
        self.crop_apply.connect("clicked", lambda *_a: self.apply_crop())
        self.crop_cancel.connect("clicked", lambda *_a: self.cancel_crop())
        self.crop_reset.connect("clicked", lambda *_a: self._reset_edit())
        self._angle_adj = self.angle_spin.get_adjustment()
        self.angle_scale.set_adjustment(self._angle_adj)
        self.angle_scale.add_mark(0.0, Gtk.PositionType.BOTTOM, None)
        self._angle_adj.connect("value-changed", self._on_angle_changed)
        self.crop_aspect.connect("notify::selected", self._on_aspect_changed)
        self.crop_swap.connect("toggled", self._on_swap_toggled)
        self.crop_guides.connect("notify::selected", self._on_guides_selected)
        for flip in (self.guide_flip_h, self.guide_flip_v):
            flip.connect("toggled", lambda *_a: self.crop_overlay.queue_draw())
        self.crop_overlay.set_draw_func(self._draw_crop_overlay)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_crop_drag_begin)
        drag.connect("drag-update", self._on_crop_drag_update)
        drag.connect("drag-end", self._on_crop_drag_end)
        self.crop_overlay.add_controller(drag)
        horizon = Gtk.GestureDrag()
        horizon.set_button(3)
        horizon.connect("drag-begin", self._on_horizon_begin)
        horizon.connect("drag-update", self._on_horizon_update)
        horizon.connect("drag-end", self._on_horizon_end)
        self.crop_overlay.add_controller(horizon)
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_crop_motion)
        self.crop_overlay.add_controller(motion)
        wheel = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES
        )
        wheel.connect("scroll", self._on_overlay_scroll)
        self.crop_overlay.add_controller(wheel)

    @property
    def rotation(self) -> int:
        """The committed 90-degree orientation, degrees clockwise."""
        return self._crop.orientation

    @property
    def crop_rotate(self) -> crop.CropRotate:
        """The current geometry (live values while editing)."""
        return self._crop

    @property
    def crop_editing(self) -> bool:
        """Whether the crop editor is active."""
        return self._editing

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
        self._oriented_pixbuf = None

    def set_crop(self, value: crop.CropRotate) -> None:
        """Set the committed geometry (for a newly selected image)."""
        if self._editing:
            self.cancel_crop()
        self._crop = value
        self._invalidate_derived()

    def show_jpeg(self, jpeg: bytes) -> bool:
        """Display JPEG bytes in the preview; False if undecodable."""
        self._last_jpeg = jpeg
        try:
            pixbuf = oriented_pixbuf(jpeg)
        except GLib.Error as exc:
            self.set_status(f"Cannot display image: {exc}")
            return False
        self.show_pixbuf(pixbuf)
        return True

    def show_pixbuf(self, pixbuf: Any, *, jpeg: bytes | None = None) -> None:
        """Display an EXIF-oriented pixbuf (jpeg is its source bytes)."""
        if jpeg is not None:
            self._last_jpeg = jpeg
        self._oriented_pixbuf = pixbuf
        self._original_pixbuf = None
        self._peek = False
        self.peek_button.set_sensitive(True)
        self.rotate_left.set_sensitive(True)
        self.rotate_right.set_sensitive(True)
        self.crop_button.set_sensitive(True)
        if self._editing:
            self._conform_crop()
        self._redisplay()

    def pixbuf_from_jpeg(self, jpeg: bytes) -> Any:
        """Decode JPEG bytes and bake the current geometry into them."""
        return bake_pixbuf(oriented_pixbuf(jpeg), self._crop)

    def rotate(self, degrees: int) -> None:
        """Turn the image by a 90-degree step and redisplay."""
        current = self._crop
        self._crop = replace(
            current,
            orientation=(current.orientation + degrees) % 360,
            rect=crop.rotate_rect_90(current.rect, degrees),
        )
        self._invalidate_derived()
        self._redisplay()
        if not self._editing:
            self.emit("geometry-changed")

    def _invalidate_derived(self) -> None:
        """Drop pixbufs derived under a now-stale geometry."""
        self._original_pixbuf = None
        self._base_pixbuf = None

    def _redisplay(self, *, histogram: bool = True) -> None:
        """Recompute the displayed image from the oriented source."""
        if self._oriented_pixbuf is None:
            return
        if not self._editing:
            display = bake_pixbuf(self._oriented_pixbuf, self._crop)
            self._pixbuf = display
            if histogram:
                self._histogram.update(display)
        self._refresh_display()
        self.crop_overlay.queue_draw()

    def _edit_display_paintable(self) -> _RotatedPaintable | None:
        """The GPU-rotated paintable for the crop editor."""
        if self._oriented_pixbuf is None:
            return None
        if self._edit_base_src is not self._oriented_pixbuf:
            self._edit_base = _capped_copy(self._oriented_pixbuf)
            self._edit_base_src = self._oriented_pixbuf
            self._edit_texture = None
        if (
            self._edit_texture is None
            or self._edit_orientation != self._crop.orientation
        ):
            pixbuf = orient_pixbuf(self._edit_base, self._crop.orientation)
            self._edit_texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            self._edit_texture_size = (
                pixbuf.get_width(),
                pixbuf.get_height(),
            )
            self._edit_orientation = self._crop.orientation
            self._edit_paintable = None
        if self._edit_paintable is None:
            self._edit_paintable = _RotatedPaintable(
                self._edit_texture, *self._edit_texture_size
            )
        self._edit_paintable.set_angle(self._crop.angle)
        return self._edit_paintable

    def _drop_edit_display(self) -> None:
        """Free the crop editor's texture and caches."""
        self._edit_base = None
        self._edit_base_src = None
        self._edit_texture = None
        self._edit_orientation = -1
        self._edit_paintable = None

    def _apply_edit_view(self) -> None:
        """Present the crop editor's GPU-rotated view at the zoom."""
        paintable = self._edit_display_paintable()
        dims = self._display_dims()
        if paintable is None or dims is None:
            return
        self._split = None
        pw, ph = dims
        vw = self.scroll.get_width() or pw
        vh = self.scroll.get_height() or ph
        if self._zoom == 1.0:
            self.picture.set_can_shrink(True)
            self.picture.set_halign(Gtk.Align.FILL)
            self.picture.set_valign(Gtk.Align.FILL)
            self.picture.set_paintable(paintable)
            self._content_w, self._content_h = float(vw), float(vh)
        else:
            fit = min(vw / pw, vh / ph)
            sw = max(1, int(pw * fit * self._zoom))
            sh = max(1, int(ph * fit * self._zoom))
            self.picture.set_can_shrink(False)
            self.picture.set_halign(Gtk.Align.CENTER)
            self.picture.set_valign(Gtk.Align.CENTER)
            self.picture.set_paintable(_ScaledPaintable(paintable, sw, sh))
            self._content_w, self._content_h = float(sw), float(sh)
        self._shown_size = (pw, ph)
        self._update_zoom_label()
        self.crop_overlay.queue_draw()

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
        fit = self._fit_scale()
        ceiling = max(1.0, MAX_NATIVE_ZOOM / fit) if fit else 8.0
        value = max(0.1, min(value, ceiling))
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

    def _fit_scale(self) -> float | None:
        """The fit-to-viewport scale of the shown image, native = 1.0."""
        dims = self._display_dims()
        if dims is None:
            return None
        pw, ph = dims
        vw, vh = self.scroll.get_width(), self.scroll.get_height()
        if pw <= 0 or ph <= 0 or vw <= 0 or vh <= 0:
            return None
        return min(vw / pw, vh / ph)

    def set_peek(self, *, peeking: bool) -> None:
        """Show the in-camera original while peeking, else the result."""
        if peeking and self._editing:
            return
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

    @property
    def comparing(self) -> bool:
        """Whether the baseline split-compare view is active."""
        return self._compare

    def set_compare_baseline(self, jpeg: bytes | None) -> None:
        """Set the baseline render (JPEG bytes) to compare against."""
        self._base_jpeg = jpeg
        self._base_pixbuf = None  # re-decode lazily at the current rotation
        if self._compare:
            self._refresh_display()

    def _base_for_rotation(self) -> Any | None:
        """The baseline pixbuf decoded at the current geometry."""
        if self._base_jpeg is None:
            return None
        if self._base_pixbuf is None or self._base_geometry != self._crop:
            try:
                self._base_pixbuf = self.pixbuf_from_jpeg(self._base_jpeg)
            except GLib.Error:
                return None
            self._base_geometry = self._crop
        return self._base_pixbuf

    def set_compare(self, *, on: bool) -> bool:
        """Turn the split-compare view on or off; returns the new state."""
        self._compare = on and self._base_jpeg is not None
        if self._compare:
            self._peek = False
        self._refresh_display()
        return self._compare

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
        """Start peeking. Claim the press so the button does not cancel it."""
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

    def _on_pan_begin(self, gesture: Any, x: float, _y: float) -> None:
        """Start a pan, or a divider drag if the grab began on the handle."""
        self._pan_h = self.scroll.get_hadjustment().get_value()
        self._pan_v = self.scroll.get_vadjustment().get_value()
        self._dragging_divider = self._compare and self._near_divider(x)
        if self._dragging_divider:
            # Own the sequence so the press cannot also fall through to
            # the overlay/toolbar widgets underneath the pointer.
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._set_resize_cursor(on=True)

    def _on_pan_update(self, gesture: Any, dx: float, dy: float) -> None:
        """Pan the preview, or move the compare divider if grabbed."""
        if self._dragging_divider:
            ok, sx, _sy = gesture.get_start_point()
            if ok:
                self._move_divider(sx + dx)
            return
        self.scroll.get_hadjustment().set_value(self._pan_h - dx)
        self.scroll.get_vadjustment().set_value(self._pan_v - dy)

    def _on_pan_end(self, _gesture: Any, _dx: float, _dy: float) -> None:
        """Finish a drag; refresh the cursor for the pointer's location."""
        was_divider = self._dragging_divider
        self._dragging_divider = False
        if was_divider:
            near = self._pointer is not None and self._near_divider(
                self._pointer[0]
            )
            self._set_resize_cursor(self._compare and near)

    def _image_rect(self) -> tuple[float, float] | None:
        """The drawn image's (left x, width) in scroll coordinates.

        Works at any zoom or scroll offset by measuring the picture's
        current bounds and applying content-fit=contain.
        """
        if self._pixbuf is None:
            return None
        iw, ih = self._pixbuf.get_width(), self._pixbuf.get_height()
        ok, rect = self.picture.compute_bounds(self.scroll)
        if not ok or iw <= 0 or ih <= 0 or rect.size.height <= 0:
            return None
        scale = min(rect.size.width / iw, rect.size.height / ih)
        drawn = iw * scale
        left = rect.origin.x + (rect.size.width - drawn) / 2
        return left, drawn

    def _near_divider(self, x: float, *, grab: float = 12.0) -> bool:
        """Whether pointer x (scroll coords) is on the divider handle."""
        image = self._image_rect()
        if image is None:
            return False
        left, drawn = image
        return abs(x - (left + self._split_fraction * drawn)) <= grab

    def _move_divider(self, x: float) -> None:
        """Set the divider from a pointer x within the drawn image rect."""
        if self._split is None:
            return
        image = self._image_rect()
        if image is None:
            return
        left, drawn = image
        self._split_fraction = max(0.0, min(1.0, (x - left) / drawn))
        self._split.set_fraction(self._split_fraction)

    def _on_pointer_motion(self, _c: Any, x: float, y: float) -> None:
        """Track the pointer; show a resize cursor over the compare handle."""
        self._pointer = (x, y)
        if not self._dragging_divider:
            self._set_resize_cursor(self._compare and self._near_divider(x))

    def _on_pointer_leave(self, _c: Any) -> None:
        """Forget the pointer and drop the resize cursor when leaving."""
        self._pointer = None
        if not self._dragging_divider:
            self._set_resize_cursor(on=False)

    def _set_resize_cursor(self, on: bool) -> None:
        """Show the horizontal-resize cursor over the divider, else default."""
        self.scroll.set_cursor(
            Gdk.Cursor.new_from_name("ew-resize", None) if on else None
        )

    def _on_scroll_zoom(self, controller: Any, _dx: float, dy: float) -> bool:
        """Zoom the preview on Ctrl+scroll."""
        event = controller.get_current_event()
        state = event.get_modifier_state() if event else 0
        if not state & Gdk.ModifierType.CONTROL_MASK:
            return False
        factor = ZOOM_STEP if dy < 0 else 1 / ZOOM_STEP
        self.set_zoom(self._zoom * factor, anchor=self._pointer)
        return True

    def _on_viewport_scrolled(self, _adj: Any) -> None:
        """Keep the crop overlay glued to the image while panning."""
        if self._editing:
            self.crop_overlay.queue_draw()

    def _on_viewport_changed(self, _adj: Any) -> None:
        """Refresh the zoom readout when the viewport geometry changes."""
        self._update_zoom_label()

    def _update_zoom_label(self) -> None:
        """Show the on-screen scale as a percentage of native pixels."""
        if self._shown_size is None:
            self.zoom_label.set_label("")
            return
        pw, ph = self._shown_size
        if self._zoom == 1.0:
            vw = self.scroll.get_width() or pw
            vh = self.scroll.get_height() or ph
            scale = min(vw / pw, vh / ph)
        else:
            scale = self._content_w / pw
        self.zoom_label.set_label(f"{scale * 100:.0f}%")

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

    def _refresh_display(self) -> None:
        """Redraw at the current zoom (split view when comparing)."""
        self._apply_zoom()

    def _preview_pixbuf(self) -> Any:
        """The pixbuf to show: the original while peeking, else the result."""
        if self._peek and self._original_pixbuf is not None:
            return self._original_pixbuf
        return self._pixbuf

    def _apply_zoom(self) -> None:
        """Show the preview at the current zoom (split view when comparing)."""
        if self._editing:
            self._apply_edit_view()
            return
        comparing_now = self._compare and not self._editing
        base_pixbuf = self._base_for_rotation() if comparing_now else None
        comparing = base_pixbuf is not None
        pixbuf = self._pixbuf if comparing else self._preview_pixbuf()
        if pixbuf is None:
            return
        pw, ph = pixbuf.get_width(), pixbuf.get_height()
        if pw <= 0 or ph <= 0:
            return
        base_tex = None
        if comparing:
            base_tex = Gdk.Texture.new_for_pixbuf(base_pixbuf)
        if self._texture_src is not pixbuf:
            self._texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            self._texture_src = pixbuf

        def paintable(width: int, height: int) -> Any:
            if base_tex is not None:
                self._split = _SplitPaintable(
                    base_tex, self._texture, width, height
                )
                self._split.set_fraction(self._split_fraction)
                return self._split
            self._split = None
            return _ScaledPaintable(self._texture, width, height)

        vw = self.scroll.get_width() or pw
        vh = self.scroll.get_height() or ph
        if self._zoom == 1.0:
            self.picture.set_can_shrink(True)
            self.picture.set_halign(Gtk.Align.FILL)
            self.picture.set_valign(Gtk.Align.FILL)
            self.picture.set_paintable(
                paintable(pw, ph) if comparing else self._texture
            )
            if not comparing:
                self._split = None
            self._content_w, self._content_h = vw, vh
            self._shown_size = (pw, ph)
            self._update_zoom_label()
            return
        fit = min(vw / pw, vh / ph)
        sw = max(1, int(pw * fit * self._zoom))
        sh = max(1, int(ph * fit * self._zoom))
        self.picture.set_can_shrink(False)
        self.picture.set_halign(Gtk.Align.CENTER)
        self.picture.set_valign(Gtk.Align.CENTER)
        self.picture.set_paintable(paintable(sw, sh))
        self._content_w, self._content_h = sw, sh
        self._shown_size = (pw, ph)
        self._update_zoom_label()

    def _on_crop_toggled(self, button: Any) -> None:
        """Enter the crop editor, or commit it when toggled back off."""
        if self._edit_closing:
            return
        if button.get_active():
            if not self._start_edit():
                self._set_crop_toggle(active=False)
        else:
            self.apply_crop()

    def _set_crop_toggle(self, *, active: bool) -> None:
        """Sync the toolbar toggle without re-entering its handler."""
        self._edit_closing = True
        self.crop_button.set_active(active)
        self._edit_closing = False

    def _start_edit(self) -> bool:
        """Open the crop editor on the current image."""
        if self._editing or self._oriented_pixbuf is None:
            return self._editing
        self._pre_edit = self._crop
        self.zoom_fit()
        self._editing = True
        self._select_aspect(self._crop.aspect)
        self._sync_swap(active=self._crop.aspect_swapped)
        self._conform_crop()
        self._sync_angle(self._crop.angle)
        self.crop_bar.set_reveal_child(True)
        self.crop_overlay.set_visible(True)
        self._redisplay(histogram=False)
        return True

    def _conform_crop(self) -> None:
        """Shrink the crop rect onto the current image if it strays."""
        dims = self._oriented_dims()
        if dims is None:
            return
        rect = crop.shrink_to_fit(
            dims[0], dims[1], self._crop.angle, self._crop.rect
        )
        if rect != self._crop.rect:
            self._crop = replace(self._crop, rect=rect)
            self.crop_overlay.queue_draw()

    def apply_crop(self) -> None:
        """Commit the crop edit (Enter, the check button or the toggle)."""
        self._finish_edit(apply=True)

    def cancel_crop(self) -> None:
        """Abandon the crop edit and restore the previous geometry."""
        self._finish_edit(apply=False)

    def _finish_edit(self, *, apply: bool) -> None:
        """Leave the crop editor, keeping or reverting its changes."""
        if not self._editing:
            return
        self._editing = False
        if not apply:
            self._crop = self._pre_edit
        self._horizon = None
        self.crop_bar.set_reveal_child(False)
        self.crop_overlay.set_visible(False)
        self._set_crop_toggle(active=False)
        self._invalidate_derived()
        self._drop_edit_display()
        self._redisplay()
        if apply and self._crop != self._pre_edit:
            self.emit("geometry-changed")

    def _reset_edit(self) -> None:
        """Reset the editor to the untouched state."""
        if not self._editing:
            return
        self._crop = replace(
            self._crop,
            angle=0.0,
            rect=crop.FULL_RECT,
            aspect="Original",
            aspect_swapped=False,
        )
        self._select_aspect("Original")
        self._sync_swap(active=False)
        self._sync_angle(0.0)
        self._redisplay(histogram=False)

    def _sync_angle(self, value: float) -> None:
        """Move the angle controls without re-applying the angle."""
        self._syncing_angle = True
        self._angle_adj.set_value(value)
        self._syncing_angle = False

    def _on_angle_changed(self, adj: Any) -> None:
        """Apply a new angle from the slider or spin button."""
        if self._syncing_angle or not self._editing:
            return
        self._set_angle(adj.get_value())

    def _oriented_dims(self) -> tuple[int, int] | None:
        """The image size in pixels after the 90-degree orientation."""
        if self._oriented_pixbuf is None:
            return None
        w = self._oriented_pixbuf.get_width()
        h = self._oriented_pixbuf.get_height()
        if self._crop.orientation in (90, 270):
            return h, w
        return w, h

    def _set_angle(self, angle: float) -> None:
        """Re-rotate to angle, carrying the crop rect along."""
        dims = self._oriented_dims()
        if dims is None:
            return
        w, h = dims
        old = self._crop
        obw, obh = crop.rotated_size(w, h, old.angle)
        nbw, nbh = crop.rotated_size(w, h, angle)
        x, y, rw, rh = old.rect
        # Keep the crop's pixel size and its offset from the image
        # center while the bounding box changes, then shrink to fit.
        ccx = (x + rw / 2 - 0.5) * obw
        ccy = (y + rh / 2 - 0.5) * obh
        nw = rw * obw / nbw
        nh = rh * obh / nbh
        nx = 0.5 + ccx / nbw - nw / 2
        ny = 0.5 + ccy / nbh - nh / 2
        rect = crop.shrink_to_fit(w, h, angle, (nx, ny, nw, nh))
        self._crop = replace(old, angle=angle, rect=rect)
        self._redisplay(histogram=False)

    @property
    def guides_name(self) -> str:
        """The selected composition-guide kind."""
        item = self.crop_guides.get_selected_item()
        return item.get_string() if item is not None else "Thirds"

    def set_crop_guides(self, name: str) -> None:
        """Select the composition guides by name."""
        model = self.crop_guides.get_model()
        index = 1  # Thirds
        for i in range(model.get_n_items()):
            if model.get_string(i) == name:
                index = i
                break
        self.crop_guides.set_selected(index)
        self._update_guide_flips()

    def _on_guides_selected(self, *_args: object) -> None:
        """Redraw for the new guides and show the flips only if useful."""
        self._update_guide_flips()
        self.crop_overlay.queue_draw()

    def _update_guide_flips(self) -> None:
        """Show the mirror toggles only for chiral guides."""
        chiral = self.guides_name in ("Spiral", "Triangles")
        self.guide_flip_h.set_visible(chiral)
        self.guide_flip_v.set_visible(chiral)

    def _aspect_label(self) -> str:
        """The aspect dropdown's current label."""
        item = self.crop_aspect.get_selected_item()
        return item.get_string() if item is not None else "Free"

    def _select_aspect(self, label: str) -> None:
        """Move the aspect dropdown without re-shaping the selection."""
        model = self.crop_aspect.get_model()
        index = 0
        for i in range(model.get_n_items()):
            if model.get_string(i) == label:
                index = i
                break
        self._syncing_aspect = True
        self.crop_aspect.set_selected(index)
        self._syncing_aspect = False

    def _aspect_ratio_px(self) -> float | None:
        """The selected crop aspect as pixel width over height, or None."""
        label = self._aspect_label()
        if label == "Free":
            ratio = None
        elif label == "Original":
            dims = self._oriented_dims()
            ratio = dims[0] / dims[1] if dims is not None else None
        else:
            a, b = label.split(":")
            ratio = float(a) / float(b)
        if ratio is not None and self.crop_swap.get_active():
            return 1.0 / ratio
        return ratio

    def _on_swap_toggled(self, _button: Any) -> None:
        """Rotate the selection between landscape and portrait."""
        if self._syncing_swap or not self._editing:
            return
        self._crop = replace(
            self._crop, aspect_swapped=self.crop_swap.get_active()
        )
        dims = self._oriented_dims()
        if dims is None:
            return
        w, h = dims
        rect = crop.swap_rect(w, h, self._crop.angle, self._crop.rect)
        self._crop = replace(self._crop, rect=rect)
        self.crop_overlay.queue_draw()

    def _sync_swap(self, *, active: bool) -> None:
        """Move the swap toggle without re-shaping the selection."""
        self._syncing_swap = True
        self.crop_swap.set_active(active)
        self._syncing_swap = False

    def _on_aspect_changed(self, *_args: object) -> None:
        """Remember the choice and re-shape the crop rect to it."""
        if self._syncing_aspect or not self._editing:
            return
        self._crop = replace(self._crop, aspect=self._aspect_label())
        ratio = self._aspect_ratio_px()
        dims = self._oriented_dims()
        if ratio is None or dims is None:
            return
        w, h = dims
        current = self._crop
        bw, bh = crop.rotated_size(w, h, current.angle)
        x, y, rw, rh = current.rect
        # Keep the center and roughly the area, in pixels.
        area = (rw * bw) * (rh * bh)
        pw = math.sqrt(area * ratio)
        ph = pw / ratio
        scale = min(1.0, bw / pw, bh / ph)
        nw = pw * scale / bw
        nh = ph * scale / bh
        nx = min(max(x + rw / 2 - nw / 2, 0.0), 1.0 - nw)
        ny = min(max(y + rh / 2 - nh / 2, 0.0), 1.0 - nh)
        rect = crop.shrink_to_fit(w, h, current.angle, (nx, ny, nw, nh))
        self._crop = replace(current, rect=rect)
        self.crop_overlay.queue_draw()

    def _display_dims(self) -> tuple[int, int] | None:
        """Logical pixel size of what the picture currently displays."""
        if self._editing:
            dims = self._oriented_dims()
            if dims is None:
                return None
            bw, bh = crop.rotated_size(dims[0], dims[1], self._crop.angle)
            return max(1, round(bw)), max(1, round(bh))
        if self._pixbuf is None:
            return None
        return self._pixbuf.get_width(), self._pixbuf.get_height()

    def _display_bounds(self) -> tuple[float, float, float, float] | None:
        """The drawn image in crop-overlay coordinates."""
        dims = self._display_dims()
        if dims is None:
            return None
        iw, ih = dims
        ok, rect = self.picture.compute_bounds(self.crop_overlay)
        if not ok or iw <= 0 or ih <= 0:
            return None
        if rect.size.width <= 0 or rect.size.height <= 0:
            return None
        scale = min(rect.size.width / iw, rect.size.height / ih)
        dw, dh = iw * scale, ih * scale
        if dw <= 0 or dh <= 0:
            return None
        return (
            rect.origin.x + (rect.size.width - dw) / 2,
            rect.origin.y + (rect.size.height - dh) / 2,
            dw,
            dh,
        )

    def _pointer_zone(self, x: float, y: float) -> str | None:
        """The crop drag zone under an overlay-coordinate pointer."""
        bounds = self._display_bounds()
        if bounds is None:
            return None
        ox, oy, dw, dh = bounds
        return crop.hit_zone(
            self._crop.rect,
            (x - ox) / dw,
            (y - oy) / dh,
            _GRAB_PX / dw,
            _GRAB_PX / dh,
        )

    def _on_overlay_scroll(
        self, controller: Any, dx: float, dy: float
    ) -> bool:
        """Zoom or pan the view from a wheel over the editor."""
        event = controller.get_current_event()
        state = event.get_modifier_state() if event else 0
        if state & Gdk.ModifierType.CONTROL_MASK:
            factor = ZOOM_STEP if dy < 0 else 1 / ZOOM_STEP
            self.set_zoom(self._zoom * factor, anchor=self._pointer)
            return True
        if state & Gdk.ModifierType.SHIFT_MASK:
            dx, dy = dy, dx
        step = 60.0
        hadj = self.scroll.get_hadjustment()
        vadj = self.scroll.get_vadjustment()
        hadj.set_value(hadj.get_value() + dx * step)
        vadj.set_value(vadj.get_value() + dy * step)
        return True

    def _on_crop_drag_begin(self, _gesture: Any, x: float, y: float) -> None:
        """Grab a handle, an edge, the rect body, or start a pan."""
        self._drag_zone = self._pointer_zone(x, y)
        self._drag_rect = self._crop.rect
        if self._drag_zone is None:
            self._pan_h = self.scroll.get_hadjustment().get_value()
            self._pan_v = self.scroll.get_vadjustment().get_value()

    def _on_crop_drag_update(
        self, _gesture: Any, dx: float, dy: float
    ) -> None:
        """Resize or move the crop rect, constrained to image pixels."""
        if self._drag_zone is None:
            self.scroll.get_hadjustment().set_value(self._pan_h - dx)
            self.scroll.get_vadjustment().set_value(self._pan_v - dy)
            return
        bounds = self._display_bounds()
        dims = self._oriented_dims()
        if bounds is None or dims is None:
            return
        _ox, _oy, dw, dh = bounds
        w, h = dims
        angle = self._crop.angle
        ratio = self._aspect_ratio_px()
        nratio = None
        if ratio is not None:
            bw, bh = crop.rotated_size(w, h, angle)
            nratio = ratio * bh / bw
        rect = crop.constrain_drag(
            w,
            h,
            angle,
            self._drag_rect,
            self._drag_zone,
            dx=dx / dw,
            dy=dy / dh,
            ratio=nratio,
        )
        self._crop = replace(self._crop, rect=rect)
        self.crop_overlay.queue_draw()

    def _on_crop_drag_end(self, _gesture: Any, _dx: float, _dy: float) -> None:
        """Release the dragged handle."""
        self._drag_zone = None

    def _on_horizon_begin(self, _gesture: Any, x: float, y: float) -> None:
        """Start drawing the straighten line."""
        self._horizon = (x, y, x, y)
        self.crop_overlay.queue_draw()

    def _on_horizon_update(self, gesture: Any, dx: float, dy: float) -> None:
        """Track the straighten line under the pointer."""
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        self._horizon = (sx, sy, sx + dx, sy + dy)
        self.crop_overlay.queue_draw()

    def _on_horizon_end(self, _gesture: Any, dx: float, dy: float) -> None:
        """Level the image to the drawn line."""
        self._horizon = None
        if abs(dx) < _MIN_HORIZON_PX and abs(dy) < _MIN_HORIZON_PX:
            self.crop_overlay.queue_draw()
            return
        angle = self._crop.angle + crop.level_delta(dx, dy)
        angle = max(-crop.MAX_ANGLE, min(crop.MAX_ANGLE, angle))
        self._sync_angle(angle)
        self._set_angle(angle)

    def _horizon_off_degrees(self, dx: float, dy: float) -> float:
        """The drawn line's on-screen tilt in degrees."""
        return -crop.level_delta(dx, dy)

    def _on_crop_motion(self, _controller: Any, x: float, y: float) -> None:
        """Track the pointer and show the zone cursor."""
        self._pointer = (x, y)
        zone = self._pointer_zone(x, y)
        if zone == self._hover_zone:
            return
        self._hover_zone = zone
        name = _ZONE_CURSORS.get(zone or "")
        self.crop_overlay.set_cursor(
            Gdk.Cursor.new_from_name(name, None) if name else None
        )

    def _draw_crop_overlay(
        self, _area: Any, ctx: Any, width: int, height: int
    ) -> None:
        """Draw the dimmed surround, guides, handles and horizon line."""
        if not self._editing:
            return
        bounds = self._display_bounds()
        if bounds is None:
            return
        ox, oy, dw, dh = bounds
        x, y, w, h = self._crop.rect
        rx, ry = ox + x * dw, oy + y * dh
        rw, rh = w * dw, h * dh
        # Dim everything outside the crop rect.
        ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        ctx.set_source_rgba(0, 0, 0, 0.45)
        ctx.rectangle(ox, oy, dw, dh)
        ctx.rectangle(rx, ry, rw, rh)
        ctx.fill()
        # Composition guides (selectable, darktable-style).
        ctx.set_line_width(1.0)
        ctx.set_source_rgba(1, 1, 1, 0.35)
        lines = crop.guide_lines(
            self.guides_name,
            rw,
            rh,
            flip_h=self.guide_flip_h.get_active(),
            flip_v=self.guide_flip_v.get_active(),
        )
        for x1, y1, x2, y2 in lines:
            ctx.move_to(rx + x1, ry + y1)
            ctx.line_to(rx + x2, ry + y2)
        ctx.stroke()
        # The rect border: a dark halo under a white line.
        ctx.set_source_rgba(0, 0, 0, 0.6)
        ctx.set_line_width(3.0)
        ctx.rectangle(rx, ry, rw, rh)
        ctx.stroke()
        ctx.set_source_rgba(1, 1, 1, 0.9)
        ctx.set_line_width(1.0)
        ctx.rectangle(rx, ry, rw, rh)
        ctx.stroke()
        # Corner and edge handles.
        ctx.set_source_rgba(1, 1, 1, 0.95)
        half = 4.0
        for hx in (rx, rx + rw / 2, rx + rw):
            for hy in (ry, ry + rh / 2, ry + rh):
                if hx == rx + rw / 2 and hy == ry + rh / 2:
                    continue
                ctx.rectangle(hx - half, hy - half, half * 2, half * 2)
        ctx.fill()
        if self._horizon is not None:
            self._draw_horizon(ctx, width, height)

    def _draw_horizon(self, ctx: Any, width: int, height: int) -> None:
        """Draw the straighten line plus its off-level degree readout."""
        if self._horizon is None:
            return
        x0, y0, x1, y1 = self._horizon
        ctx.set_source_rgba(1.0, 0.8, 0.2, 0.9)
        ctx.set_line_width(2.0)
        ctx.move_to(x0, y0)
        ctx.line_to(x1, y1)
        ctx.stroke()
        for ex, ey in ((x0, y0), (x1, y1)):
            ctx.arc(ex, ey, 3.0, 0.0, 2.0 * math.pi)
            ctx.stroke()
        dx, dy = x1 - x0, y1 - y0
        if abs(dx) < _MIN_HORIZON_PX and abs(dy) < _MIN_HORIZON_PX:
            return
        label = f"{self._horizon_off_degrees(dx, dy):.2f}°"
        ctx.set_font_size(13.0)
        ext = ctx.text_extents(label)
        pad = 5.0
        tx = x1 + 16.0
        ty = y1 - 16.0
        tx = min(max(tx, pad), width - ext.width - 2 * pad)
        ty = min(max(ty, ext.height + 2 * pad), height - pad)
        ctx.set_source_rgba(0, 0, 0, 0.65)
        ctx.rectangle(
            tx - pad,
            ty - ext.height - pad,
            ext.width + 2 * pad,
            ext.height + 2 * pad,
        )
        ctx.fill()
        ctx.set_source_rgba(1, 1, 1, 0.95)
        ctx.move_to(tx - ext.x_bearing, ty)
        ctx.show_text(label)
