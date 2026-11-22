"""Tests for the image load controller."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

gi = pytest.importorskip("gi")

from gi.repository import GLib

from grawji.controllers import image_load
from grawji.controllers.image_load import (
    Decoded,
    ImageLoadController,
    LoadHost,
)
from tests.image_support import jpeg_bytes, quadrants


class FakeSession:
    """The camera session, reduced to the close a camera file asks for."""

    def close(self) -> None:
        """Stand in for closing the open RAF."""


class FakeWorker:
    """A camera worker that records what it was asked to do."""

    def __init__(self) -> None:
        """Start with nothing queued."""
        self.opened: list[tuple[str, Callable[[Any], None], Any]] = []
        self.submitted: list[Callable[[], Any]] = []

    def open(
        self,
        raf_path: str,
        *,
        on_done: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        """Queue opening a RAF."""
        self.opened.append((raf_path, on_done, on_error))

    def submit(self, task: Callable[[], Any]) -> None:
        """Queue an arbitrary camera task."""
        self.submitted.append(task)


@dataclass
class Window:
    """The window's side of the load, recording what reaches it."""

    generation: int = 1
    camera: bool = True
    camera_files: dict[str, str] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    decoded: list[Decoded] = field(default_factory=list)

    def host(self) -> LoadHost:
        """The callbacks the controller is given."""
        return LoadHost(
            generation=lambda: self.generation,
            camera_file=self.camera_files.get,
            camera_present=lambda: self.camera,
            on_begin=lambda path: self.events.append(f"begin {path}"),
            on_decoded=self.decoded.append,
            on_opened=lambda: self.events.append("opened"),
            on_no_camera=lambda: self.events.append("no camera"),
            on_error=lambda exc: self.events.append("error"),
            on_quiet_error=lambda exc: self.events.append("quiet error"),
        )


@dataclass
class Rig:
    """A controller with its fakes, decoding inline."""

    window: Window
    worker: FakeWorker
    controller: ImageLoadController

    def load(self, path: str, *, quiet: bool = False) -> None:
        """Select an image and wait for its load to start."""
        self.controller.select(path, quiet=quiet)
        self.settle()

    def settle(self) -> None:
        """Run the main loop past the selection delay."""
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            context.iteration(may_block=False)
            time.sleep(0.005)


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Rig:
    """A controller over a fake camera, with a readable embedded JPEG."""
    jpeg = jpeg_bytes(quadrants(), str(tmp_path / "embedded.jpg"), tag=1)
    monkeypatch.setattr(image_load.raf, "embedded_jpeg", lambda path: jpeg)
    monkeypatch.setattr(
        image_load.imagemeta, "native_size", lambda path: (6000, 4000)
    )
    monkeypatch.setattr(
        image_load.imagemeta, "exif_rows", lambda data: [("ISO", "200")]
    )
    window = Window()
    worker = FakeWorker()
    controller = ImageLoadController(
        FakeSession(),
        worker,
        window.host(),
        spawn=lambda task: task(),
        dispatch=lambda callback: callback(),
    )
    return Rig(window, worker, controller)


def test_a_raf_goes_to_the_camera_and_shows_its_own_picture(rig: Rig) -> None:
    """The open and the decode of one selection both start."""
    rig.load("/shots/a.RAF")

    assert rig.window.events == ["begin /shots/a.RAF"]
    assert [path for path, _done, _error in rig.worker.opened] == [
        "/shots/a.RAF"
    ]
    shown = rig.window.decoded[0]
    assert shown.pixbuf is not None
    assert shown.rows == [("ISO", "200")]
    assert shown.native == (6000, 4000)

    rig.worker.opened[0][1](None)
    assert rig.window.events[-1] == "opened"


def test_scrubbing_loads_only_the_last_selection(rig: Rig) -> None:
    """Images passed over within the delay are never loaded."""
    for name in ("a", "b", "c"):
        rig.controller.select(f"/shots/{name}.RAF")
    rig.settle()

    assert rig.window.events == ["begin /shots/c.RAF"]
    assert len(rig.worker.opened) == 1


def test_a_selection_replaced_before_it_loads_is_skipped(rig: Rig) -> None:
    """A newer selection made elsewhere cancels the pending load."""
    rig.controller.select("/shots/a.RAF")
    rig.window.generation = 2
    rig.settle()

    assert rig.window.events == []
    assert rig.worker.opened == []


def test_an_open_for_an_older_selection_is_dropped(rig: Rig) -> None:
    """A camera open that returns after the user moved on is ignored."""
    rig.load("/shots/a.RAF")
    rig.window.generation = 2

    rig.worker.opened[0][1](None)

    assert "opened" not in rig.window.events


def test_without_a_camera_the_raf_is_only_shown(rig: Rig) -> None:
    """No camera means no open, the picture still appears."""
    rig.window.camera = False

    rig.load("/shots/a.RAF")

    assert rig.worker.opened == []
    assert rig.window.events == ["begin /shots/a.RAF", "no camera"]
    assert len(rig.window.decoded) == 1


def test_a_camera_file_closes_the_session_instead_of_opening(
    rig: Rig, tmp_path: Path
) -> None:
    """A shot developed from its camera file never goes to the camera."""
    camera_file = tmp_path / "a.JPG"
    camera_file.write_bytes(
        jpeg_bytes(quadrants(), str(tmp_path / "source.jpg"), tag=1)
    )
    rig.window.camera_files["/shots/a.RAF"] = str(camera_file)

    rig.load("/shots/a.RAF")

    assert rig.worker.opened == []
    assert len(rig.worker.submitted) == 1
    assert rig.window.decoded[0].pixbuf is not None


def test_an_unreadable_picture_still_reports_its_size(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A RAF without a usable embedded JPEG clears the picture."""

    def broken(path: str) -> bytes:
        raise ValueError("no embedded JPEG")

    monkeypatch.setattr(image_load.raf, "embedded_jpeg", broken)

    rig.load("/shots/a.RAF")

    assert rig.window.decoded == [Decoded(None, None, [], (6000, 4000))]


def test_a_quiet_load_reports_its_failure_quietly(rig: Rig) -> None:
    """An automatic reconnect that fails raises no dialog."""
    rig.load("/shots/a.RAF", quiet=True)

    rig.worker.opened[0][2](OSError("could not connect"))

    assert rig.window.events[-1] == "quiet error"
