"""Controller for loading a selected image."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib

from grawji import mainloop, raf
from grawji.camera.core import CameraSession
from grawji.camera.preview import CameraWorker
from grawji.imaging import imagemeta
from grawji.views.preview_view import oriented_pixbuf

# A selection waits this long before it loads, so fast scrubbing does
# not start a decode for every image passed over.
_LOAD_DELAY_MS = 50

Spawn = Callable[[Callable[[], None]], Any]


def _spawn_thread(task: Callable[[], None]) -> None:
    """Run a decode on a short-lived thread."""
    threading.Thread(target=task, name="grawji-decode", daemon=True).start()


@dataclass(frozen=True)
class Decoded:
    """An image's own picture, read from the file.

    Attributes:
        jpeg: The embedded or camera JPEG.
        pixbuf: The JPEG decoded upright.
        rows: The EXIF rows for the info panel.
        native: The image's pixel size.
    """

    jpeg: bytes | None
    pixbuf: Any
    rows: list[tuple[str, str]]
    native: tuple[int, int] | None


@dataclass(frozen=True)
class LoadHost:
    """What loading an image needs from the window.

    Attributes:
        generation: The current image selection's number.
        camera_file: The camera file an image is developed from.
        camera_present: Whether a camera is there to open the RAF on.
        on_begin: Called with the path when its load starts.
        on_decoded: Takes the image's own picture.
        on_opened: Called once the camera has the RAF open.
        on_no_camera: Called when the RAF stays unopened for lack of a
            camera.
        on_error: Takes a failed camera open.
        on_quiet_error: Takes a failed open nobody asked for.
    """

    generation: Callable[[], int]
    camera_file: Callable[[str], str | None]
    camera_present: Callable[[], bool]
    on_begin: Callable[[str], None]
    on_decoded: Callable[[Decoded], None]
    on_opened: Callable[[], None]
    on_no_camera: Callable[[], None]
    on_error: Callable[[Exception], None]
    on_quiet_error: Callable[[Exception], None]


class ImageLoadController:
    """Loads the selected image: its own picture and the camera open."""

    def __init__(
        self,
        session: CameraSession,
        worker: CameraWorker,
        host: LoadHost,
        *,
        spawn: Spawn = _spawn_thread,
        dispatch: mainloop.Dispatch = mainloop.call,
    ) -> None:
        """Bind the controller to the camera and the window.

        Args:
            session: The camera session a camera file closes.
            worker: The camera worker the open is queued on.
            host: The window's side of the load.
            spawn: Runs a decode off the main thread.
            dispatch: Brings a decoded result back to the main thread.
        """
        self._session = session
        self._worker = worker
        self._host = host
        self._spawn = spawn
        self._dispatch = dispatch
        self._pending_id = 0

    def select(self, raf_path: str, *, quiet: bool = False) -> None:
        """Load an image once the selection settles.

        Args:
            raf_path: The image to load.
            quiet: A failed camera open is logged instead of shown.
        """
        if self._pending_id:
            GLib.source_remove(self._pending_id)
        self._pending_id = GLib.timeout_add(
            _LOAD_DELAY_MS,
            self._load,
            self._host.generation(),
            raf_path,
            quiet,
        )

    def _load(self, generation: int, raf_path: str, quiet: bool) -> bool:
        """Start the camera open and the decode of a settled selection."""
        self._pending_id = 0
        if generation != self._host.generation():
            return bool(GLib.SOURCE_REMOVE)
        self._host.on_begin(raf_path)
        source = self._host.camera_file(raf_path)
        if source is not None:
            self._worker.submit(self._session.close)
        elif self._host.camera_present():
            self._worker.open(
                raf_path,
                on_done=partial(self._on_opened, generation),
                on_error=(
                    self._host.on_quiet_error if quiet else self._host.on_error
                ),
            )
        else:
            self._host.on_no_camera()
        self._spawn(partial(self._decode, generation, raf_path, source))
        return bool(GLib.SOURCE_REMOVE)

    def _decode(
        self, generation: int, raf_path: str, source: str | None
    ) -> None:
        """Read and decode the image's own picture."""
        native = imagemeta.native_size(source or raf_path)
        try:
            jpeg = (
                Path(source).read_bytes()
                if source is not None
                else raf.embedded_jpeg(raf_path)
            )
            decoded = Decoded(
                jpeg, oriented_pixbuf(jpeg), imagemeta.exif_rows(jpeg), native
            )
        except (ValueError, OSError, GLib.Error):
            decoded = Decoded(None, None, [], native)
        self._dispatch(partial(self._apply, generation, decoded))

    def _apply(self, generation: int, decoded: Decoded) -> None:
        """Hand the picture to the window unless it has moved on."""
        if generation == self._host.generation():
            self._host.on_decoded(decoded)

    def _on_opened(self, generation: int, _result: object) -> None:
        """Report the open unless a newer selection has replaced it."""
        if generation == self._host.generation():
            self._host.on_opened()
