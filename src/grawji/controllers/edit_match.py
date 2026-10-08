"""Find the recipe closest to an edited export of the open shot."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, GLib

from grawji import edit_align, look_match, mainloop
from grawji.imaging.pixbufs import crop_to_aspect, rgb_array, trim_letterbox
from grawji.imaging.render import oriented_jpeg, scale_to_edge

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


@dataclass
class EditMatchCallbacks:
    """Main-thread callbacks of one search."""

    on_edit: Callable[[NDArray[np.uint8]], None]
    on_view: Callable[[NDArray[np.uint8]], None]
    on_progress: Callable[[Recipe, int], None]
    on_stage: Callable[[str], None]
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
        edit = load_edit(request.edit_path)
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

        def render(recipe: Recipe) -> NDArray[np.uint8]:
            jpeg = self._session.render_thumb(recipe)
            pixels = rgb_array(
                crop_to_aspect(oriented_jpeg(jpeg, request.orientation), ratio)
            )
            latest["recipe"], latest["pixels"] = recipe, pixels
            return pixels

        def progress(recipe: Recipe, renders: int) -> None:
            mainloop.call(callbacks.on_progress, recipe, renders)
            if latest.get("recipe") == recipe:
                view = alignment.warp(latest["pixels"], size)
                mainloop.call(callbacks.on_view, view)

        def stage(name: str) -> None:
            mainloop.call(callbacks.on_stage, name)

        target = look_match.LookTarget(
            edit, alignment, (frame.shape[1], frame.shape[0])
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
            ),
        )


def load_edit(path: str) -> NDArray[np.uint8]:
    """The edited export, upright, at the comparison size."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    except GLib.Error as exc:
        msg = "The picked file could not be read as an image."
        raise EditMatchError(msg) from exc
    pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
    return rgb_array(scale_to_edge(pixbuf, EDIT_EDGE))
