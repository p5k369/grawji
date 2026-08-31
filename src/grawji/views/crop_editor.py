"""Controller for the crop and straighten editor."""

from __future__ import annotations

import math
import threading
from typing import TYPE_CHECKING, Any

import cairo
import gi

gi.require_version("Gtk", "4.0")

from dataclasses import replace

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from grawji import crop, level
from grawji.views.render import bake_pixbuf, gray_rows

if TYPE_CHECKING:
    from grawji.views.preview_view import PreviewView

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

# Longer edge of the small bake analyzed by auto level, and of the
# grayscale the edge analysis actually runs on.
_LEVEL_BAKE_PX = 800
_LEVEL_ANALYSIS_PX = 640


class CropEditor:
    """Drives the crop/straighten editing session on a PreviewView."""

    def __init__(self, view: PreviewView) -> None:
        """Wire the crop bar, the overlay drawing and its gestures."""
        self._view = view
        self.editing = False
        self._pre_edit = crop.CropRotate()
        self._edit_closing = False
        self._syncing_angle = False
        self._syncing_swap = False
        self._syncing_aspect = False
        self._drag_zone: str | None = None
        self._drag_rect: crop.Rect = crop.FULL_RECT
        self._hover_zone: str | None = None
        self._pointer: tuple[float, float] | None = None
        self._horizon: tuple[float, float, float, float] | None = None
        self._auto_candidates: list[level.Candidate] = []
        self._auto_base: crop.CropRotate | None = None
        self._auto_expected: crop.CropRotate | None = None
        self._auto_index = 0
        self._auto_lines: list[tuple[float, float, float, float]] = []

        # Bound view helpers, so the editing logic below reads naturally.
        self._redisplay = view._redisplay
        self._update_size_label = view._update_size_label
        self._oriented_dims = view._oriented_dims
        self._display_bounds = view._display_bounds
        self._invalidate_derived = view._invalidate_derived
        self._drop_edit_display = view._drop_edit_display
        self.set_status = view.set_status

        self._wire_controls(view)
        self._wire_gestures(view)

    def _wire_controls(self, view: PreviewView) -> None:
        """Connect the crop bar's buttons, dropdowns and the angle."""
        view.crop_button.connect("toggled", self._on_crop_toggled)
        view.crop_apply.connect("clicked", lambda *_a: self.apply())
        view.crop_cancel.connect("clicked", lambda *_a: self.cancel())
        view.crop_reset.connect("clicked", lambda *_a: self.reset())
        self._angle_adj = view.angle_spin.get_adjustment()
        view.angle_scale.set_adjustment(self._angle_adj)
        view.angle_scale.add_mark(0.0, Gtk.PositionType.BOTTOM, None)
        self._angle_adj.connect("value-changed", self._on_angle_changed)
        view.auto_level_button.connect("clicked", self._on_auto_level)
        view.crop_aspect.connect("notify::selected", self._on_aspect_changed)
        view.crop_swap.connect("toggled", self._on_swap_toggled)
        view.crop_guides.connect("notify::selected", self._on_guides_selected)
        for flip in (view.guide_flip_h, view.guide_flip_v):
            flip.connect("toggled", lambda *_a: view.crop_overlay.queue_draw())
        view.crop_overlay.set_draw_func(self._draw_crop_overlay)

    def _wire_gestures(self, view: PreviewView) -> None:
        """Attach the overlay's drag, horizon, motion and wheel input."""
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_crop_drag_begin)
        drag.connect("drag-update", self._on_crop_drag_update)
        drag.connect("drag-end", self._on_crop_drag_end)
        view.crop_overlay.add_controller(drag)
        horizon = Gtk.GestureDrag()
        horizon.set_button(3)
        horizon.connect("drag-begin", self._on_horizon_begin)
        horizon.connect("drag-update", self._on_horizon_update)
        horizon.connect("drag-end", self._on_horizon_end)
        view.crop_overlay.add_controller(horizon)
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_crop_motion)
        view.crop_overlay.add_controller(motion)
        wheel = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES
        )
        wheel.connect("scroll", self._on_overlay_scroll)
        view.crop_overlay.add_controller(wheel)

    @property
    def geometry(self) -> crop.CropRotate:
        """The geometry being edited."""
        return self._view._crop

    @geometry.setter
    def geometry(self, value: crop.CropRotate) -> None:
        self._view._crop = value

    def _on_crop_toggled(self, button: Any) -> None:
        """Enter the crop editor, or commit it when toggled back off."""
        if self._edit_closing:
            return
        if button.get_active():
            if not self._start_edit():
                self._set_crop_toggle(active=False)
        else:
            self.apply()

    def _set_crop_toggle(self, *, active: bool) -> None:
        """Sync the toolbar toggle without re-entering its handler."""
        self._edit_closing = True
        self._view.crop_button.set_active(active)
        self._edit_closing = False

    def _start_edit(self) -> bool:
        """Open the crop editor on the current image."""
        if self.editing or self._view._oriented_pixbuf is None:
            return self.editing
        self._pre_edit = self.geometry
        self._view.zoom_fit()
        self.editing = True
        self._select_aspect(self.geometry.aspect)
        self._sync_swap(active=self.geometry.aspect_swapped)
        self.conform()
        self._sync_angle(self.geometry.angle)
        self._view.crop_bar.set_reveal_child(True)
        self._view.crop_overlay.set_visible(True)
        self._redisplay(histogram=False)
        return True

    def conform(self) -> None:
        """Shrink the crop rect onto the current image if it strays."""
        dims = self._oriented_dims()
        if dims is None:
            return
        rect = crop.shrink_to_fit(
            dims[0], dims[1], self.geometry.angle, self.geometry.rect
        )
        if rect != self.geometry.rect:
            self.geometry = replace(self.geometry, rect=rect)
            self._view.crop_overlay.queue_draw()

    def apply(self) -> None:
        """Commit the crop edit."""
        self._finish_edit(apply=True)

    def cancel(self) -> None:
        """Abandon the crop edit and restore the previous geometry."""
        self._finish_edit(apply=False)

    def _finish_edit(self, *, apply: bool) -> None:
        """Leave the crop editor, keeping or reverting its changes."""
        if not self.editing:
            return
        self.editing = False
        if not apply:
            self.geometry = self._pre_edit
        self._horizon = None
        self._reset_auto_level()
        self._view.crop_bar.set_reveal_child(False)
        self._view.crop_overlay.set_visible(False)
        self._set_crop_toggle(active=False)
        self._invalidate_derived()
        self._drop_edit_display()
        self._redisplay()
        if apply and self.geometry != self._pre_edit:
            self._view.emit("geometry-changed")

    def reset(self) -> None:
        """Reset the editor to the untouched state."""
        if not self.editing:
            return
        self.geometry = replace(
            self.geometry,
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
        if self._syncing_angle or not self.editing:
            return
        self._set_angle(adj.get_value())

    def _set_angle(self, angle: float) -> None:
        """Re-rotate to angle, carrying the crop rect along."""
        self._auto_lines = []
        dims = self._oriented_dims()
        if dims is None:
            return
        w, h = dims
        old = self.geometry
        obw, obh = crop.rotated_size(w, h, old.angle)
        nbw, nbh = crop.rotated_size(w, h, angle)
        x, y, rw, rh = old.rect
        ccx = (x + rw / 2 - 0.5) * obw
        ccy = (y + rh / 2 - 0.5) * obh
        nw = rw * obw / nbw
        nh = rh * obh / nbh
        nx = 0.5 + ccx / nbw - nw / 2
        ny = 0.5 + ccy / nbh - nh / 2
        rect = crop.shrink_to_fit(w, h, angle, (nx, ny, nw, nh))
        self.geometry = replace(old, angle=angle, rect=rect)
        self._redisplay(histogram=False)

    @property
    def guides_name(self) -> str:
        """The selected composition-guide kind."""
        item = self._view.crop_guides.get_selected_item()
        return item.get_string() if item is not None else "Thirds"

    def set_guides(self, name: str) -> None:
        """Select the composition guides by name."""
        model = self._view.crop_guides.get_model()
        index = 1
        for i in range(model.get_n_items()):
            if model.get_string(i) == name:
                index = i
                break
        self._view.crop_guides.set_selected(index)
        self._update_guide_flips()

    def _on_guides_selected(self, *_args: object) -> None:
        """Redraw for the new guides and show the flips only if useful."""
        self._update_guide_flips()
        self._view.crop_overlay.queue_draw()

    def _update_guide_flips(self) -> None:
        """Show the mirror toggles only for chiral guides."""
        chiral = self.guides_name in ("Spiral", "Triangles")
        self._view.guide_flip_h.set_visible(chiral)
        self._view.guide_flip_v.set_visible(chiral)

    def _aspect_label(self) -> str:
        """The aspect dropdown's current label."""
        item = self._view.crop_aspect.get_selected_item()
        return item.get_string() if item is not None else "Free"

    def _select_aspect(self, label: str) -> None:
        """Move the aspect dropdown without re-shaping the selection."""
        model = self._view.crop_aspect.get_model()
        index = 0
        for i in range(model.get_n_items()):
            if model.get_string(i) == label:
                index = i
                break
        self._syncing_aspect = True
        self._view.crop_aspect.set_selected(index)
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
        if ratio is not None and self._view.crop_swap.get_active():
            return 1.0 / ratio
        return ratio

    def _on_swap_toggled(self, _button: Any) -> None:
        """Rotate the selection between landscape and portrait."""
        if self._syncing_swap or not self.editing:
            return
        self.geometry = replace(
            self.geometry, aspect_swapped=self._view.crop_swap.get_active()
        )
        dims = self._oriented_dims()
        if dims is None:
            return
        w, h = dims
        rect = crop.swap_rect(w, h, self.geometry.angle, self.geometry.rect)
        self.geometry = replace(self.geometry, rect=rect)
        self._view.crop_overlay.queue_draw()
        self._update_size_label()

    def _sync_swap(self, *, active: bool) -> None:
        """Move the swap toggle without re-shaping the selection."""
        self._syncing_swap = True
        self._view.crop_swap.set_active(active)
        self._syncing_swap = False

    def _on_aspect_changed(self, *_args: object) -> None:
        """Remember the choice and re-shape the crop rect to it."""
        if self._syncing_aspect or not self.editing:
            return
        self.geometry = replace(self.geometry, aspect=self._aspect_label())
        ratio = self._aspect_ratio_px()
        dims = self._oriented_dims()
        if ratio is None or dims is None:
            return
        w, h = dims
        current = self.geometry
        bw, bh = crop.rotated_size(w, h, current.angle)
        x, y, rw, rh = current.rect
        area = (rw * bw) * (rh * bh)
        pw = math.sqrt(area * ratio)
        ph = pw / ratio
        scale = min(1.0, bw / pw, bh / ph)
        nw = pw * scale / bw
        nh = ph * scale / bh
        nx = min(max(x + rw / 2 - nw / 2, 0.0), 1.0 - nw)
        ny = min(max(y + rh / 2 - nh / 2, 0.0), 1.0 - nh)
        rect = crop.shrink_to_fit(w, h, current.angle, (nx, ny, nw, nh))
        self.geometry = replace(current, rect=rect)
        self._view.crop_overlay.queue_draw()
        self._update_size_label()

    def _pointer_zone(self, x: float, y: float) -> str | None:
        """The crop drag zone under an overlay-coordinate pointer."""
        bounds = self._display_bounds()
        if bounds is None:
            return None
        ox, oy, dw, dh = bounds
        return crop.hit_zone(
            self.geometry.rect,
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
            self._view.zoom_step(1 if dy < 0 else -1, anchor=self._pointer)
            return True
        if state & Gdk.ModifierType.SHIFT_MASK:
            dx, dy = dy, dx
        step = 60.0
        hadj = self._view.scroll.get_hadjustment()
        vadj = self._view.scroll.get_vadjustment()
        hadj.set_value(hadj.get_value() + dx * step)
        vadj.set_value(vadj.get_value() + dy * step)
        return True

    def _on_crop_drag_begin(self, _gesture: Any, x: float, y: float) -> None:
        """Grab a handle, an edge, the rect body, or start a pan."""
        self._drag_zone = self._pointer_zone(x, y)
        self._drag_rect = self.geometry.rect
        if self._drag_zone is None:
            self._view.begin_overlay_pan()

    def _on_crop_drag_update(
        self, _gesture: Any, dx: float, dy: float
    ) -> None:
        """Resize or move the crop rect, constrained to image pixels."""
        if self._drag_zone is None:
            self._view.update_overlay_pan(dx, dy)
            return
        bounds = self._display_bounds()
        dims = self._oriented_dims()
        if bounds is None or dims is None:
            return
        _ox, _oy, dw, dh = bounds
        w, h = dims
        angle = self.geometry.angle
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
        self.geometry = replace(self.geometry, rect=rect)
        self._view.crop_overlay.queue_draw()
        self._update_size_label()

    def _on_crop_drag_end(self, _gesture: Any, _dx: float, _dy: float) -> None:
        """Release the dragged handle."""
        self._drag_zone = None

    def _on_horizon_begin(self, _gesture: Any, x: float, y: float) -> None:
        """Start drawing the straighten line."""
        self._horizon = (x, y, x, y)
        self._view.crop_overlay.queue_draw()

    def _on_horizon_update(self, gesture: Any, dx: float, dy: float) -> None:
        """Track the straighten line under the pointer."""
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        self._horizon = (sx, sy, sx + dx, sy + dy)
        self._view.crop_overlay.queue_draw()

    def _on_horizon_end(self, _gesture: Any, dx: float, dy: float) -> None:
        """Level the image to the drawn line."""
        self._horizon = None
        if abs(dx) < _MIN_HORIZON_PX and abs(dy) < _MIN_HORIZON_PX:
            self._view.crop_overlay.queue_draw()
            return
        angle = self.geometry.angle + crop.level_delta(dx, dy)
        angle = max(-crop.MAX_ANGLE, min(crop.MAX_ANGLE, angle))
        self._sync_angle(angle)
        self._set_angle(angle)

    def _horizon_off_degrees(self, dx: float, dy: float) -> float:
        """The drawn line's on-screen tilt in degrees."""
        return -crop.level_delta(dx, dy)

    def _on_auto_level(self, *_args: object) -> None:
        """Level to a detected line family, cycling on repeat clicks."""
        if not self.editing or self._view._oriented_pixbuf is None:
            return
        if (
            len(self._auto_candidates) > 1
            and self._auto_expected is not None
            and self.geometry == self._auto_expected
        ):
            self._auto_index = (self._auto_index + 1) % len(
                self._auto_candidates
            )
            self._apply_auto_candidate()
            return
        self._view.auto_level_button.set_sensitive(False)
        threading.Thread(
            target=self._auto_level_work,
            args=(self._view._oriented_pixbuf, self.geometry),
            name="grawji-auto-level",
            daemon=True,
        ).start()

    def _auto_level_work(self, source: Any, state: crop.CropRotate) -> None:
        """Compute the level suggestions for state."""
        width = source.get_width()
        height = source.get_height()
        scale = _LEVEL_BAKE_PX / max(width, height)
        if scale < 1.0:
            source = source.scale_simple(
                max(1, round(width * scale)),
                max(1, round(height * scale)),
                GdkPixbuf.InterpType.BILINEAR,
            )
        baked = bake_pixbuf(source, state)
        candidates = level.suggest_candidates(
            gray_rows(baked, _LEVEL_ANALYSIS_PX)
        )
        GLib.idle_add(self._finish_auto_level, state, candidates)

    def _finish_auto_level(
        self, state: crop.CropRotate, candidates: list[level.Candidate]
    ) -> bool:
        """Store fresh suggestions and apply the first."""
        self._view.auto_level_button.set_sensitive(True)
        if not self.editing or self.geometry != state:
            return GLib.SOURCE_REMOVE
        if not candidates:
            self.set_status("No level suggestion found.")
            return GLib.SOURCE_REMOVE
        self._auto_base = state
        self._auto_candidates = candidates
        self._auto_index = 0
        self._apply_auto_candidate()
        return GLib.SOURCE_REMOVE

    def _apply_auto_candidate(self) -> None:
        """Apply the current suggestion relative to the analyzed state."""
        base = self._auto_base
        if base is None:
            return
        candidate = self._auto_candidates[self._auto_index]
        angle = base.angle + candidate.delta
        angle = max(-crop.MAX_ANGLE, min(crop.MAX_ANGLE, angle))
        self._sync_angle(angle)
        self._set_angle(angle)
        self._auto_expected = self.geometry
        self._mark_auto_lines(base, candidate, angle)
        count = len(self._auto_candidates)
        more = ", click again for the next line" if count > 1 else ""
        self.set_status(
            f"Auto level {self._auto_index + 1}/{count}:"
            f" {candidate.delta:+.2f}°{more}"
        )

    def _mark_auto_lines(
        self,
        base: crop.CropRotate,
        candidate: level.Candidate,
        angle: float,
    ) -> None:
        """Show the lines this suggestion leveled to."""
        dims = self._oriented_dims()
        if dims is None:
            return
        w, h = dims
        lines = []
        for x0, y0, x1, y1 in candidate.segments:
            ax, ay = crop.reframe_point(
                (x0, y0), w, h, base.angle, base.rect, angle
            )
            bx, by = crop.reframe_point(
                (x1, y1), w, h, base.angle, base.rect, angle
            )
            lines.append((ax, ay, bx, by))
        self._auto_lines = lines
        self._view.crop_overlay.queue_draw()

    def _reset_auto_level(self) -> None:
        """Drop cached auto-level suggestions."""
        self._auto_candidates = []
        self._auto_base = None
        self._auto_expected = None
        self._auto_index = 0
        self._auto_lines = []

    def _on_crop_motion(self, _controller: Any, x: float, y: float) -> None:
        """Track the pointer and show the zone cursor."""
        self._pointer = (x, y)
        zone = self._pointer_zone(x, y)
        if zone == self._hover_zone:
            return
        self._hover_zone = zone
        name = _ZONE_CURSORS.get(zone or "")
        self._view.crop_overlay.set_cursor(
            Gdk.Cursor.new_from_name(name, None) if name else None
        )

    def _draw_crop_overlay(
        self, _area: Any, ctx: Any, width: int, height: int
    ) -> None:
        """Draw the dimmed surround, guides, handles and horizon line."""
        if not self.editing:
            return
        bounds = self._display_bounds()
        if bounds is None:
            return
        ox, oy, dw, dh = bounds
        x, y, w, h = self.geometry.rect
        rx, ry = ox + x * dw, oy + y * dh
        rw, rh = w * dw, h * dh
        ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        ctx.set_source_rgba(0, 0, 0, 0.45)
        ctx.rectangle(ox, oy, dw, dh)
        ctx.rectangle(rx, ry, rw, rh)
        ctx.fill()
        ctx.set_line_width(1.0)
        ctx.set_source_rgba(1, 1, 1, 0.35)
        lines = crop.guide_lines(
            self.guides_name,
            rw,
            rh,
            flip_h=self._view.guide_flip_h.get_active(),
            flip_v=self._view.guide_flip_v.get_active(),
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
        ctx.set_source_rgba(1, 1, 1, 0.95)
        half = 4.0
        for hx in (rx, rx + rw / 2, rx + rw):
            for hy in (ry, ry + rh / 2, ry + rh):
                if hx == rx + rw / 2 and hy == ry + rh / 2:
                    continue
                ctx.rectangle(hx - half, hy - half, half * 2, half * 2)
        ctx.fill()
        if self._auto_lines:
            ctx.save()
            ctx.rectangle(ox, oy, dw, dh)
            ctx.clip()
            ctx.set_source_rgba(0.24, 0.82, 0.44, 0.9)
            ctx.set_line_width(2.0)
            for ax, ay, bx, by in self._auto_lines:
                ctx.move_to(ox + ax * dw, oy + ay * dh)
                ctx.line_to(ox + bx * dw, oy + by * dh)
            ctx.stroke()
            ctx.restore()
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
