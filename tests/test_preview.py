"""Tests for the CameraWorker threading/coalescing layer."""

import threading

from grawji.preview import CameraWorker
from grawji.recipe import Recipe


def notify(event, sink=None):
    """Callback that records the value (if a sink is given) and signals."""

    def callback(value=None):
        """Record the value (if a sink is given) and set the event."""
        if sink is not None:
            sink.append(value)
        event.set()

    return callback


class FakeSession:
    """A CameraSession stand-in; render can be gated to test ordering."""

    def __init__(self):
        """Start with empty logs and no gate or forced failure."""
        self.opened = []
        self.rendered = []
        self.closed = False
        self.gate = None  # optional Event to block inside render()
        self.render_started = threading.Event()
        self.fail_render = False
        self.open_gate = None  # optional Event to block inside open()
        self.open_started = threading.Event()

    def open(self, raf_path):
        """Signal start, optionally block on the gate, then record."""
        self.open_started.set()
        if self.open_gate is not None:
            self.open_gate.wait(timeout=5)
        self.opened.append(raf_path)

    def render(self, recipe, *, full_resolution):
        """Signal start, optionally block on the gate, then record/return."""
        self.render_started.set()
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if self.fail_render:
            raise RuntimeError("render boom")
        self.rendered.append((recipe.film_simulation, full_resolution))
        return b"JPEG:" + recipe.film_simulation.encode()

    def close(self):
        """Mark the session as closed."""
        self.closed = True


def test_open_then_render_delivers_result():
    """An open then a render run in order and deliver the JPEG result."""
    sess = FakeSession()
    results = []
    done = threading.Event()

    worker = CameraWorker(sess)
    worker.start()
    worker.open("/x.RAF")
    worker.render(
        Recipe(film_simulation="Velvia"),
        full_resolution=False,
        on_done=notify(done, results),
    )
    assert done.wait(timeout=5)
    worker.stop()

    assert sess.opened == ["/x.RAF"]  # open never coalesced away
    assert sess.rendered == [("Velvia", False)]
    assert results == [b"JPEG:Velvia"]
    assert sess.closed  # stop() closed the session


def test_render_coalesces_pending_render():
    """A render queued behind a not-yet-started render replaces it."""
    sess = FakeSession()
    sess.gate = threading.Event()
    done = threading.Event()
    finished = []

    worker = CameraWorker(sess)
    worker.start()

    worker.render(Recipe(film_simulation="Provia"), full_resolution=False)
    assert sess.render_started.wait(timeout=5)

    worker.render(Recipe(film_simulation="Velvia"), full_resolution=False)
    worker.render(
        Recipe(film_simulation="Acros"),
        full_resolution=True,
        on_done=notify(done, finished),
    )

    sess.gate.set()
    assert done.wait(timeout=5)
    worker.stop()

    assert sess.rendered == [("Provia", False), ("Acros", True)]
    assert finished == [b"JPEG:Acros"]


def test_open_coalesces_pending_open():
    """An open queued behind a not-yet-started open replaces it."""
    sess = FakeSession()
    sess.open_gate = threading.Event()
    done = threading.Event()

    worker = CameraWorker(sess)
    worker.start()

    worker.open("/a.RAF")  # starts, blocks on the gate
    assert sess.open_started.wait(timeout=5)

    worker.open("/b.RAF")  # queued
    worker.open("/c.RAF", on_done=notify(done))  # replaces /b.RAF

    sess.open_gate.set()
    assert done.wait(timeout=5)
    worker.stop()

    assert sess.opened == ["/a.RAF", "/c.RAF"]  # /b.RAF coalesced away


def test_render_does_not_coalesce_a_pending_open():
    """A render never replaces a queued open of a different kind."""
    sess = FakeSession()
    sess.open_gate = threading.Event()
    done = threading.Event()

    worker = CameraWorker(sess)
    worker.start()

    worker.open("/a.RAF")  # starts, blocks on the gate
    assert sess.open_started.wait(timeout=5)

    worker.open("/b.RAF")  # queued open
    worker.render(
        Recipe(film_simulation="Velvia"),
        full_resolution=False,
        on_done=notify(done),
    )  # must NOT replace the queued open

    sess.open_gate.set()
    assert done.wait(timeout=5)
    worker.stop()

    assert sess.opened == ["/a.RAF", "/b.RAF"]  # open survived
    assert sess.rendered == [("Velvia", False)]


def test_render_error_routed_to_on_error():
    """A render exception is delivered to on_error, not swallowed."""
    sess = FakeSession()
    sess.fail_render = True
    errors = []
    done = threading.Event()

    worker = CameraWorker(sess)
    worker.start()
    worker.render(
        Recipe(),
        full_resolution=False,
        on_error=notify(done, errors),
    )
    assert done.wait(timeout=5)
    worker.stop()

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert sess.rendered == []


def test_submit_runs_arbitrary_task():
    """submit() runs a non-coalesced task and delivers its result."""
    sess = FakeSession()
    done = threading.Event()
    results = []

    worker = CameraWorker(sess)
    worker.start()
    worker.submit(lambda: "batch-done", on_done=notify(done, results))
    assert done.wait(timeout=5)
    worker.stop()

    assert results == ["batch-done"]


def test_context_manager_starts_and_closes():
    """The context manager starts the worker and closes on exit."""
    sess = FakeSession()
    done = threading.Event()

    with CameraWorker(sess) as worker:
        worker.open("/y.RAF", on_done=notify(done))
        assert done.wait(timeout=5)

    assert sess.opened == ["/y.RAF"]
    assert sess.closed  # __exit__ -> stop() -> close()
