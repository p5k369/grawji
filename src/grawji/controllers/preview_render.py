"""Controller for the live preview render."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib

from grawji import mainloop
from grawji.camera.core import CameraSession
from grawji.camera.preview import CameraWorker
from grawji.imaging import imagemeta
from grawji.imaging.pixbufs import trim_letterbox
from grawji.imaging.render import oriented_jpeg, thumb_jpeg
from grawji.recipe import Recipe
from grawji.views.preview_view import oriented_pixbuf

_DEBOUNCE_MS = 150
_RECIPE_THUMB_EDGE = 640

Spawn = Callable[[Callable[[], None]], Any]


def _spawn_thread(task: Callable[[], None]) -> None:
    """Run a decode on a short-lived thread."""
    threading.Thread(
        target=task, name="grawji-preview-decode", daemon=True
    ).start()


@dataclass(frozen=True)
class PreviewHost:
    """What the preview render needs from the window.

    Attributes:
        generation: The current image selection's number.
        recipe: The recipe the controls show.
        baseline: The recipe to compare against.
        comparing: Whether the split view is switched on.
        on_settled: Called once an edit has settled, before it renders.
        show_preview: Takes the decoded render.
        show_baseline: Takes the baseline render as a JPEG.
        set_busy: Takes the busy flag and the status line.
        on_error: Takes a failed camera operation.
    """

    generation: Callable[[], int]
    recipe: Callable[[], Recipe]
    baseline: Callable[[], Recipe | None]
    comparing: Callable[[], bool]
    on_settled: Callable[[], None]
    show_preview: Callable[[Any], None]
    show_baseline: Callable[[bytes], None]
    set_busy: Callable[[bool, str], None]
    on_error: Callable[[Exception], None]


class PreviewRenderController:
    """Renders the open image for the preview and the split view."""

    def __init__(
        self,
        session: CameraSession,
        worker: CameraWorker,
        host: PreviewHost,
        *,
        spawn: Spawn = _spawn_thread,
        dispatch: mainloop.Dispatch = mainloop.call,
    ) -> None:
        """Bind the controller to the camera and the window.

        Args:
            session: The camera session, asked whether an image is open.
            worker: The camera worker the renders are queued on.
            host: The window's side of the render.
            spawn: Runs a decode off the main thread.
            dispatch: Brings a decoded result back to the main thread.
        """
        self._session = session
        self._worker = worker
        self._host = host
        self._spawn = spawn
        self._dispatch = dispatch
        self._pending_id = 0
        self._seq = 0

    def schedule(self) -> None:
        """Render once the edits settle."""
        if self._pending_id:
            GLib.source_remove(self._pending_id)
        self._pending_id = GLib.timeout_add(_DEBOUNCE_MS, self._render_now)

    def flush(self) -> None:
        """Settle a pending edit now, without rendering it."""
        if self._pending_id:
            GLib.source_remove(self._pending_id)
            self._pending_id = 0
            self._host.on_settled()

    def render_if_open(self) -> None:
        """Render the preview if an image is open."""
        if self._session.is_open:
            self.render()

    def render(self) -> None:
        """Queue a fast preview render of the current recipe."""
        self._host.set_busy(True, "Rendering preview…")
        self._worker.render(
            self._host.recipe(),
            full_resolution=False,
            on_done=partial(self._on_preview, self._host.generation()),
            on_error=self._host.on_error,
        )

    def render_baseline(self) -> None:
        """Render the baseline recipe for the split view."""
        baseline = self._host.baseline()
        if baseline is None:
            return
        baseline = replace(baseline, exposure=self._host.recipe().exposure)
        self._host.set_busy(True, "Preparing comparison…")
        self._worker.submit(
            partial(self._session.render, baseline, full_resolution=False),
            on_done=partial(
                self._on_baseline_rendered, self._host.generation()
            ),
            on_error=self._host.on_error,
        )

    def render_thumb(
        self,
        raf_path: str,
        recipe: Recipe,
        on_done: Callable[[bytes | None], None],
    ) -> None:
        """Render a recipe against a RAF into a small upright thumbnail.

        Args:
            raf_path: The image the recipe is rendered on.
            recipe: The recipe to render.
            on_done: Takes the thumbnail as a JPEG.
        """
        generation = self._host.generation()

        def task() -> bytes | None:
            if not self._session.is_open:
                self._session.open(raf_path)
            jpeg = self._session.render_thumb(recipe)
            try:
                upright = oriented_jpeg(
                    jpeg, imagemeta.exif_orientation(raf_path)
                )
                return thumb_jpeg(
                    trim_letterbox(upright), max_edge=_RECIPE_THUMB_EDGE
                )
            except GLib.Error:
                return None

        def finished(thumb: bytes | None) -> None:
            current = generation == self._host.generation()
            on_done(thumb if current else None)

        def failed(exc: Exception) -> None:
            self._host.on_error(exc)
            on_done(None)

        self._worker.submit(task, on_done=finished, on_error=failed)

    def _render_now(self) -> bool:
        """Fire the debounced render."""
        self._pending_id = 0
        self._host.on_settled()
        self.render_if_open()
        return bool(GLib.SOURCE_REMOVE)

    def _on_preview(self, generation: int, jpeg: bytes) -> None:
        """Decode a finished render off the main thread."""
        if generation != self._host.generation():
            return
        self._seq += 1
        self._spawn(partial(self._decode, generation, self._seq, jpeg))

    def _decode(self, generation: int, seq: int, jpeg: bytes) -> None:
        """Decode the render and hand it back to the main thread."""
        try:
            pixbuf = oriented_pixbuf(jpeg)
        except GLib.Error as exc:
            self._dispatch(partial(self._apply, generation, seq, None, exc))
            return
        self._dispatch(partial(self._apply, generation, seq, pixbuf, None))

    def _apply(
        self, generation: int, seq: int, pixbuf: Any, error: Exception | None
    ) -> None:
        """Show a decoded render unless a newer one has replaced it."""
        if generation != self._host.generation() or seq != self._seq:
            return
        if pixbuf is None:
            self._host.set_busy(False, f"Cannot display image: {error}")
            return
        self._host.show_preview(pixbuf)
        self._host.set_busy(False, "Ready.")

    def _on_baseline_rendered(self, generation: int, jpeg: bytes) -> None:
        """Feed the baseline render into the split view."""
        if generation != self._host.generation():
            return
        if not self._host.comparing():
            self._host.set_busy(False, "Ready.")
            return
        self._host.show_baseline(jpeg)
        self._host.set_busy(False, "Comparing with baseline.")
