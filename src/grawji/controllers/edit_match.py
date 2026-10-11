"""Find the recipe closest to an edited export of the open shot."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, GLib

from grawji import edit_align, look_match, mainloop
from grawji.imaging.pixbufs import crop_to_aspect, rgb_array, trim_letterbox
from grawji.imaging.render import oriented_jpeg, scale_to_edge
from grawji.search_estimate import SearchEstimate
from grawji.texture_match import TextureTarget

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    from grawji.camera.capabilities import Capabilities
    from grawji.camera.core import CameraSession
    from grawji.camera.preview import CameraWorker
    from grawji.recipe import Recipe

# The edit is aligned and compared at this long edge.
EDIT_EDGE = 384


class EditMatchError(Exception):
    """The search could not start."""


@dataclass(frozen=True)
class EditMatchRequest:
    """What one search needs."""

    raf_path: str
    edit_path: str
    orientation: int
    start: Recipe
    capabilities: Capabilities
    skin_priority: bool = False
    texture: bool = False


@dataclass
class EditMatchCallbacks:
    """Main-thread callbacks of one search."""

    on_edit: Callable[[NDArray[np.uint8]], None]
    on_view: Callable[[NDArray[np.uint8]], None]
    on_progress: Callable[[Recipe, int], None]
    on_stage: Callable[[str], None]
    on_estimate: Callable[[float, float | None], None]
    on_done: Callable[[look_match.Match], None]
    on_error: Callable[[Exception], None]


class EditMatchController:
    """Runs recipe-from-edit searches on the camera worker."""

    def __init__(self, session: CameraSession, worker: CameraWorker) -> None:
        """Bind the controller to the camera session and its worker."""
        self._session = session
        self._worker = worker

    def start(
        self, request: EditMatchRequest, callbacks: EditMatchCallbacks
    ) -> Callable[[], None]:
        """Queue a search and return a function that cancels it."""
        stop = threading.Event()

        def task() -> look_match.Match:
            return self._run(request, callbacks, stop)

        self._worker.submit(
            task, on_done=callbacks.on_done, on_error=callbacks.on_error
        )
        return stop.set

    def _run(
        self,
        request: EditMatchRequest,
        callbacks: EditMatchCallbacks,
        stop: threading.Event,
    ) -> look_match.Match:
        """The whole search, on the camera worker thread."""
        pixbuf = load_edit(request.edit_path)
        edit = small_edit(pixbuf)
        mainloop.call(callbacks.on_edit, edit)
        if not self._session.is_open:
            self._session.open(request.raf_path)
        first = oriented_jpeg(
            self._session.render_thumb(request.start), request.orientation
        )
        trimmed = trim_letterbox(first)
        ratio = trimmed.get_width() / trimmed.get_height()
        frame = rgb_array(crop_to_aspect(first, ratio))
        alignment = edit_align.align(frame, edit)
        if alignment is None:
            msg = "The picked image does not show the open shot."
            raise EditMatchError(msg)
        size = (edit.shape[1], edit.shape[0])
        mainloop.call(callbacks.on_view, alignment.warp(frame, size))
        latest: dict[str, Any] = {}
        estimate = SearchEstimate(
            request.capabilities, time.monotonic(), texture=request.texture
        )

        def report() -> None:
            mainloop.call(
                callbacks.on_estimate, estimate.fraction, estimate.seconds_left
            )

        def render(recipe: Recipe) -> NDArray[np.uint8]:
            jpeg = self._session.render_thumb(recipe)
            pixels = rgb_array(
                crop_to_aspect(oriented_jpeg(jpeg, request.orientation), ratio)
            )
            latest["recipe"], latest["pixels"] = recipe, pixels
            estimate.rendered(time.monotonic(), clarity=recipe.clarity != 0)
            report()
            return pixels

        def render_full(recipe: Recipe) -> NDArray[np.uint8]:
            jpeg = self._session.render(recipe, full_resolution=True)
            pixels = rgb_array(
                crop_to_aspect(oriented_jpeg(jpeg, request.orientation), ratio)
            )
            estimate.rendered(time.monotonic(), texture=True)
            report()
            return pixels

        def progress(recipe: Recipe, renders: int) -> None:
            mainloop.call(callbacks.on_progress, recipe, renders)
            if latest.get("recipe") == recipe:
                view = alignment.warp(latest["pixels"], size)
                mainloop.call(callbacks.on_view, view)

        def stage(name: str) -> None:
            estimate.enter(name)
            mainloop.call(callbacks.on_stage, name)

        frame_size = (frame.shape[1], frame.shape[0])
        target = look_match.LookTarget(
            edit, alignment, frame_size, skin_priority=request.skin_priority
        )
        texture = None
        if request.texture:
            texture = look_match.TextureSearch(
                render_full,
                TextureTarget(rgb_array(pixbuf), alignment, size, frame_size),
            )
        return look_match.search(
            request.start,
            request.capabilities,
            render,
            target,
            look_match.SearchHooks(
                progress=progress,
                stage=stage,
                cancelled=stop.is_set,
                candidates=estimate.plan,
            ),
            texture=texture,
        )


def load_edit(path: str) -> Any:
    """The edited export as an upright pixbuf at its full size."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    except GLib.Error as exc:
        msg = "The picked file could not be read as an image."
        raise EditMatchError(msg) from exc
    return pixbuf.apply_embedded_orientation() or pixbuf


def small_edit(pixbuf: Any) -> NDArray[np.uint8]:
    """An edit at the size the look is aligned and compared at."""
    return rgb_array(scale_to_edge(pixbuf, EDIT_EDGE))


def edit_preview(path: str) -> NDArray[np.uint8]:
    """The edited export, upright, at the comparison size."""
    return small_edit(load_edit(path))
