"""Tests for the preview render controller."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

gi = pytest.importorskip("gi")

from gi.repository import GLib

from grawji.controllers.preview_render import (
    PreviewHost,
    PreviewRenderController,
)
from grawji.recipe import Recipe
from tests.image_support import jpeg_bytes, quadrants


class FakeSession:
    """The camera session, reduced to what the render asks of it."""

    def __init__(self) -> None:
        """Start with an image open."""
        self.is_open = True
        self.rendered: list[Recipe] = []
        self.opened: list[str] = []
        self.thumb = b""

    def open(self, raf_path: str) -> None:
        """Open an image the way the manager's thumbnail job does."""
        self.opened.append(raf_path)
        self.is_open = True

    def render_thumb(self, recipe: Recipe) -> bytes:
        """Hand out the canned camera thumbnail."""
        self.rendered.append(recipe)
        return self.thumb

    def render(self, recipe: Recipe, *, full_resolution: bool) -> bytes:
        """Remember the recipe a baseline render was asked for."""
        assert not full_resolution
        self.rendered.append(recipe)
        return b"baseline"


class FakeWorker:
    """A camera worker that holds its jobs until the test finishes them."""

    def __init__(self) -> None:
        """Start with nothing queued."""
        self.renders: list[tuple[Recipe, Callable[[bytes], None]]] = []
        self.tasks: list[tuple[Callable[[], Any], Callable[[Any], None]]] = []

    def render(
        self,
        recipe: Recipe,
        *,
        full_resolution: bool,
        on_done: Callable[[bytes], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        """Queue a preview render."""
        assert not full_resolution
        self.renders.append((recipe, on_done))

    def submit(
        self,
        task: Callable[[], Any],
        *,
        on_done: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        """Queue an arbitrary camera task."""
        self.tasks.append((task, on_done))


@dataclass
class Window:
    """The window's side of the render, recording what reaches it."""

    generation: int = 1
    recipe: Recipe = field(default_factory=Recipe)
    baseline: Recipe | None = None
    comparing: bool = True
    settled: int = 0
    shown: list[Any] = field(default_factory=list)
    baselines: list[bytes] = field(default_factory=list)
    status: list[tuple[bool, str]] = field(default_factory=list)

    def settle(self) -> None:
        """Count an edit that settled."""
        self.settled += 1

    def host(self) -> PreviewHost:
        """The callbacks the controller is given."""
        return PreviewHost(
            generation=lambda: self.generation,
            recipe=lambda: self.recipe,
            baseline=lambda: self.baseline,
            comparing=lambda: self.comparing,
            on_settled=self.settle,
            show_preview=self.shown.append,
            show_baseline=self.baselines.append,
            set_busy=lambda busy, status: self.status.append((busy, status)),
            on_error=lambda exc: pytest.fail(str(exc)),
        )


@dataclass
class Rig:
    """A controller with its fakes, decoding inline."""

    window: Window
    session: FakeSession
    worker: FakeWorker
    controller: PreviewRenderController
    decodes: list[Callable[[], None]]


def make(*, hold_decodes: bool = False) -> Rig:
    """Build a controller whose decode runs inline or on request."""
    window = Window()
    session = FakeSession()
    worker = FakeWorker()
    decodes: list[Callable[[], None]] = []
    controller = PreviewRenderController(
        session,
        worker,
        window.host(),
        spawn=decodes.append if hold_decodes else lambda task: task(),
        dispatch=lambda callback: callback(),
    )
    return Rig(window, session, worker, controller, decodes)


@pytest.fixture
def jpeg(tmp_path: Path) -> bytes:
    """A small real JPEG, as the camera would return one."""
    return jpeg_bytes(quadrants(), str(tmp_path / "render.jpg"), tag=1)


def settle(rig: Rig) -> None:
    """Run the main loop until the debounce has fired."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + 2.0
    while not rig.window.settled and time.monotonic() < deadline:
        context.iteration(may_block=False)
        time.sleep(0.005)


def test_a_render_is_decoded_and_shown(jpeg: bytes) -> None:
    """A finished render reaches the window and clears the busy state."""
    rig = make()
    rig.controller.render()
    assert rig.window.status == [(True, "Rendering preview…")]

    rig.worker.renders[0][1](jpeg)

    assert len(rig.window.shown) == 1
    assert rig.window.status[-1] == (False, "Ready.")


def test_a_render_for_an_older_selection_is_dropped(jpeg: bytes) -> None:
    """A render that returns after the user moved on is not shown."""
    rig = make()
    rig.controller.render()
    rig.window.generation = 2

    rig.worker.renders[0][1](jpeg)

    assert rig.window.shown == []


def test_an_older_decode_does_not_replace_a_newer_one(jpeg: bytes) -> None:
    """Of two renders decoding at once, only the later one is shown."""
    rig = make(hold_decodes=True)
    rig.controller.render()
    rig.controller.render()
    for _recipe, done in rig.worker.renders:
        done(jpeg)

    older, newer = rig.decodes
    newer()
    older()

    assert len(rig.window.shown) == 1


def test_an_undecodable_render_is_reported() -> None:
    """A render that is no image ends in a status, not in a picture."""
    rig = make()
    rig.controller.render()

    rig.worker.renders[0][1](b"not a jpeg")

    assert rig.window.shown == []
    busy, status = rig.window.status[-1]
    assert not busy
    assert status.startswith("Cannot display image")


def test_render_if_open_needs_an_open_image() -> None:
    """Nothing is queued while no image is open on the camera."""
    rig = make()
    rig.session.is_open = False

    rig.controller.render_if_open()

    assert rig.worker.renders == []


def test_edits_in_quick_succession_render_once() -> None:
    """A slider drag settles into one render."""
    rig = make()
    for _ in range(5):
        rig.controller.schedule()

    settle(rig)

    assert rig.window.settled == 1
    assert len(rig.worker.renders) == 1


def test_flush_settles_a_pending_edit_without_rendering() -> None:
    """Leaving an image stores the pending edit and skips its render."""
    rig = make()
    rig.controller.schedule()

    rig.controller.flush()
    rig.controller.flush()

    assert rig.window.settled == 1
    assert rig.worker.renders == []


def test_the_baseline_renders_at_the_image_exposure() -> None:
    """The split view compares both recipes at the same exposure."""
    rig = make()
    rig.window.recipe = Recipe(exposure=1.0)
    rig.window.baseline = Recipe(film_simulation="Velvia", exposure=-1.0)

    rig.controller.render_baseline()
    task, done = rig.worker.tasks[0]
    done(task())

    assert rig.session.rendered == [
        Recipe(film_simulation="Velvia", exposure=1.0)
    ]
    assert rig.window.baselines == [b"baseline"]
    assert rig.window.status[-1] == (False, "Comparing with baseline.")


def test_no_baseline_means_no_render() -> None:
    """Without a marked baseline the camera is left alone."""
    rig = make()

    rig.controller.render_baseline()

    assert rig.worker.tasks == []
    assert rig.window.status == []


def test_a_baseline_is_dropped_once_compare_is_off() -> None:
    """Switching the split off while it renders keeps it off."""
    rig = make()
    rig.window.baseline = Recipe()
    rig.controller.render_baseline()
    rig.window.comparing = False

    rig.worker.tasks[0][1](b"baseline")

    assert rig.window.baselines == []
    assert rig.window.status[-1] == (False, "Ready.")


def test_a_baseline_for_an_older_selection_is_dropped() -> None:
    """A baseline that returns after the user moved on is not shown."""
    rig = make()
    rig.window.baseline = Recipe()
    rig.controller.render_baseline()
    rig.window.generation = 2

    rig.worker.tasks[0][1](b"baseline")

    assert rig.window.baselines == []


def test_a_recipe_thumbnail_reopens_a_closed_image(jpeg: bytes) -> None:
    """The manager closes the session, the thumbnail job opens it again."""
    rig = make()
    rig.session.is_open = False
    rig.session.thumb = jpeg
    results: list[bytes | None] = []
    recipe = Recipe(film_simulation="Velvia")

    rig.controller.render_thumb("/shots/a.RAF", recipe, results.append)
    task, done = rig.worker.tasks[0]
    done(task())

    assert rig.session.opened == ["/shots/a.RAF"]
    assert rig.session.rendered == [recipe]
    assert results[0] is not None
    assert results[0][:2] == b"\xff\xd8"


def test_a_recipe_thumbnail_for_an_older_selection_is_dropped(
    jpeg: bytes,
) -> None:
    """A thumbnail that returns after the user moved on is not stored."""
    rig = make()
    results: list[bytes | None] = []
    rig.controller.render_thumb("/shots/a.RAF", Recipe(), results.append)
    rig.window.generation = 2

    rig.worker.tasks[0][1](jpeg)

    assert results == [None]


def test_an_undecodable_recipe_thumbnail_is_reported() -> None:
    """A thumbnail that is no image ends in no picture, not in an error."""
    rig = make()
    rig.session.thumb = b"not a jpeg"
    results: list[bytes | None] = []

    rig.controller.render_thumb("/shots/a.RAF", Recipe(), results.append)
    task, done = rig.worker.tasks[0]
    done(task())

    assert results == [None]
