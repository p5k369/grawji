"""The preview viewport: zoom, pan, peek, crop, background, histogram."""

from __future__ import annotations

from dataclasses import replace
from importlib import resources
from typing import Any, ClassVar

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import (
    Gdk,
    GdkPixbuf,
    Gio,
    GLib,
    GObject,
    Gtk,
)

from grawji import crop
from grawji.imaging.render import (
    add_border,
    bake_pixbuf,
    orient_pixbuf,
    parse_aspect,
    texture_for_pixbuf,
)
from grawji.views.crop_editor import CropEditor
from grawji.views.paintables import (
    RotatedPaintable,
    ScaledPaintable,
    SplitPaintable,
)
from grawji.views.widgets import Histogram

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
    size_label = Gtk.Template.Child()
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
    auto_level_button = Gtk.Template.Child()
    crop_reset = Gtk.Template.Child()
    crop_cancel = Gtk.Template.Child()
    crop_apply = Gtk.Template.Child()

    def __init__(self, **kwargs: object) -> None:
        """Wire the zoom, pan, peek and crop controllers."""
        super().__init__(**kwargs)
        self._zoom = 1.0
        self._init_edit_state()
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
        self._split: SplitPaintable | None = None
        self._split_fraction = 0.5
        self._dragging_divider = False
        self._shown_size: tuple[int, int] | None = None

        self.rotate_left.connect("clicked", lambda *_a: self.rotate(-90))
        self.rotate_right.connect("clicked", lambda *_a: self.rotate(90))
        self.status.connect("activate-link", self._on_status_link)
        self._crop_editor = CropEditor(self)

        self._histogram = Histogram()
        self._histogram.set_hexpand(True)
        self._histogram.set_vexpand(True)
        self.histogram_slot.append(self._histogram)
        self._init_viewport_controllers()

    def _init_edit_state(self) -> None:
        """Initialise the geometry and edit-display state fields."""
        self._crop = crop.CropRotate()
        self._border_percent = 0.0
        self._border_color = "#ffffff"
        self._border_aspect = "None"
        self._native_dims: tuple[int, int] | None = None
        self._native_locked = False
        self._display_base: Any | None = None
        self._display_key: tuple[Any, crop.CropRotate] | None = None
        self._edit_base: Any | None = None
        self._edit_base_src: Any | None = None
        self._edit_texture: Any = None
        self._edit_texture_size = (0, 0)
        self._edit_orientation = -1
        self._edit_paintable: RotatedPaintable | None = None

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
        return self._crop_editor.editing

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
        self.status.set_use_markup(False)
        self.status.set_label(text)

    def set_status_link(self, text: str, path: str) -> None:
        """Set a status line that opens path's location when clicked."""
        uri = GLib.markup_escape_text(GLib.filename_to_uri(path))
        label = GLib.markup_escape_text(text)
        self.status.set_markup(f'<a href="{uri}">{label}</a>')

    def _on_status_link(self, _label: Any, uri: str) -> bool:
        """Open the linked file's folder (or the folder itself)."""
        target = Gio.File.new_for_uri(uri)
        launcher = Gtk.FileLauncher.new(target)
        parent = self.get_root()
        window = parent if isinstance(parent, Gtk.Window) else None
        is_dir = (
            target.query_file_type(Gio.FileQueryInfoFlags.NONE, None)
            == Gio.FileType.DIRECTORY
        )
        if is_dir:
            launcher.launch(window, None, None)
        else:
            launcher.open_containing_folder(window, None, None)
        return True

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
        if self._crop_editor.editing:
            self.cancel_crop()
        self._crop = value
        self._native_dims = None
        self._native_locked = False
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
        dims = (pixbuf.get_width(), pixbuf.get_height())
        if not self._native_locked and (
            self._native_dims is None
            or dims[0] * dims[1] > self._native_dims[0] * self._native_dims[1]
        ):
            self._native_dims = dims
        self._original_pixbuf = None
        self._peek = False
        self.peek_button.set_sensitive(True)
        self.rotate_left.set_sensitive(True)
        self.rotate_right.set_sensitive(True)
        self.crop_button.set_sensitive(True)
        if self._crop_editor.editing:
            self._crop_editor.conform()
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
        if not self._crop_editor.editing:
            self.emit("geometry-changed")

    def _invalidate_derived(self) -> None:
        """Drop pixbufs derived under a now-stale geometry."""
        self._original_pixbuf = None
        self._base_pixbuf = None

    def set_export_border(
        self, percent: float, color: str, aspect: str = "None"
    ) -> None:
        """Show the configured export framing on the committed preview."""
        state = (percent, color, aspect)
        if state == (
            self._border_percent,
            self._border_color,
            self._border_aspect,
        ):
            return
        self._border_percent, self._border_color, self._border_aspect = state
        self._invalidate_derived()
        self._redisplay()

    def _bordered(self, pixbuf: Any) -> Any:
        """Apply the export framing for display purposes."""
        return add_border(
            pixbuf,
            self._border_percent,
            self._border_color,
            parse_aspect(self._border_aspect),
        )

    def _redisplay(self, *, histogram: bool = True) -> None:
        """Recompute the displayed image from the oriented source."""
        if self._oriented_pixbuf is None:
            return
        if not self._crop_editor.editing:
            key = (self._oriented_pixbuf, self._crop)
            if self._display_key != key:
                self._display_base = bake_pixbuf(
                    self._oriented_pixbuf, self._crop
                )
                self._display_key = key
                if histogram:
                    # The histogram describes the image, not the border.
                    self._histogram.update(self._display_base)
            self._pixbuf = self._bordered(self._display_base)
        self._refresh_display()
        self._update_size_label()
        self.crop_overlay.queue_draw()

    def set_native_size(self, dims: tuple[int, int] | None) -> None:
        """Set the image's native (oriented) pixel size from metadata."""
        if dims is not None:
            self._native_dims = dims
            self._native_locked = True
        self._update_size_label()

    def _update_size_label(self) -> None:
        """Show the image's pixel size after the crop."""
        if self._native_dims is None:
            self.size_label.set_label("")
            return
        w, h = self._native_dims
        if self._crop.orientation in (90, 270):
            w, h = h, w
        bw, bh = crop.rotated_size(w, h, self._crop.angle)
        px_w = max(1, round(self._crop.rect[2] * bw))
        px_h = max(1, round(self._crop.rect[3] * bh))
        mp = px_w * px_h / 1e6
        text = f"{px_w} × {px_h}   {mp:.1f} MP"  # noqa: RUF001
        self.size_label.set_label(text)

    def _edit_display_paintable(self) -> RotatedPaintable | None:
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
            self._edit_texture = texture_for_pixbuf(pixbuf)
            self._edit_texture_size = (
                pixbuf.get_width(),
                pixbuf.get_height(),
            )
            self._edit_orientation = self._crop.orientation
            self._edit_paintable = None
        if self._edit_paintable is None:
            self._edit_paintable = RotatedPaintable(
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
            self.picture.set_paintable(ScaledPaintable(paintable, sw, sh))
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
        if peeking and self._crop_editor.editing:
            return
        if peeking and self._original_pixbuf is None:
            if self._embedded_jpeg is None:
                return
            try:
                self._original_pixbuf = self._bordered(
                    self.pixbuf_from_jpeg(self._embedded_jpeg)
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
                self._base_pixbuf = self._bordered(
                    self.pixbuf_from_jpeg(self._base_jpeg)
                )
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
        if self._crop_editor.editing:
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
        if self._crop_editor.editing:
            self._apply_edit_view()
            return
        comparing_now = self._compare and not self._crop_editor.editing
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
            base_tex = texture_for_pixbuf(base_pixbuf)
        if self._texture_src is not pixbuf:
            self._texture = texture_for_pixbuf(pixbuf)
            self._texture_src = pixbuf

        def paintable(width: int, height: int) -> Any:
            if base_tex is not None:
                self._split = SplitPaintable(
                    base_tex, self._texture, width, height
                )
                self._split.set_fraction(self._split_fraction)
                return self._split
            self._split = None
            return ScaledPaintable(self._texture, width, height)

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

    def apply_crop(self) -> None:
        """Commit the crop edit (Enter, the check button or the toggle)."""
        self._crop_editor.apply()

    def cancel_crop(self) -> None:
        """Abandon the crop edit and restore the previous geometry."""
        self._crop_editor.cancel()

    @property
    def guides_name(self) -> str:
        """The selected composition-guide kind."""
        return self._crop_editor.guides_name

    def set_crop_guides(self, name: str) -> None:
        """Select the composition guides by name."""
        self._crop_editor.set_guides(name)

    def zoom_step(
        self, direction: int, anchor: tuple[float, float] | None = None
    ) -> None:
        """Zoom one step in (direction > 0) or out, around anchor."""
        factor = ZOOM_STEP if direction > 0 else 1 / ZOOM_STEP
        self.set_zoom(self._zoom * factor, anchor=anchor)

    def begin_overlay_pan(self) -> None:
        """Remember the scroll position at the start of an overlay pan."""
        self._pan_h = self.scroll.get_hadjustment().get_value()
        self._pan_v = self.scroll.get_vadjustment().get_value()

    def update_overlay_pan(self, dx: float, dy: float) -> None:
        """Pan the viewport by a drag delta from the overlay."""
        self.scroll.get_hadjustment().set_value(self._pan_h - dx)
        self.scroll.get_vadjustment().set_value(self._pan_v - dy)

    def _oriented_dims(self) -> tuple[int, int] | None:
        """The image size in pixels after the 90-degree orientation."""
        if self._oriented_pixbuf is None:
            return None
        w = self._oriented_pixbuf.get_width()
        h = self._oriented_pixbuf.get_height()
        if self._crop.orientation in (90, 270):
            return h, w
        return w, h

    def _display_dims(self) -> tuple[int, int] | None:
        """Logical pixel size of what the picture currently displays."""
        if self._crop_editor.editing:
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
