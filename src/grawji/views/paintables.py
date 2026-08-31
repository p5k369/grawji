"""Custom paintables for the preview: scaled, split-compare, rotated."""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GObject, Graphene

from grawji import crop


class ScaledPaintable(GObject.GObject, Gdk.Paintable):
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


class SplitPaintable(GObject.GObject, Gdk.Paintable):
    """Two textures split by a movable vertical divider."""

    def __init__(self, base: Any, work: Any, width: int, height: int) -> None:
        """Compose base and work at width x height."""
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


class RotatedPaintable(GObject.GObject, Gdk.Paintable):
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
