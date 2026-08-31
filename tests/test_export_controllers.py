"""Tests for Export controllers."""

from __future__ import annotations

from typing import Any

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf

import grawji.controllers.exports as module
from grawji.camera.core import ForeignRafError
from grawji.controllers.exports import (
    BatchController,
    SingleExportController,
)
from grawji.recipe import Recipe
from grawji.settings import Settings


def small_jpeg() -> bytes:
    """A tiny valid camera-JPEG stand-in."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 6)
    pixbuf.fill(0x808080FF)
    ok, data = pixbuf.save_to_bufferv("jpeg", [], [])
    assert ok
    return bytes(data)


def single_controller(state, settings=None):
    """A SingleExportController over recording fakes."""
    return SingleExportController(
        parent=None,
        worker=None,
        settings=settings or Settings(),
        save_settings=lambda: None,
        get_recipe=Recipe,
        get_current_raf=lambda: None,
        base_decode=lambda jpeg: GdkPixbuf.Pixbuf.new(
            GdkPixbuf.Colorspace.RGB, False, 8, 4, 3
        ),
        set_busy=lambda **kw: state.setdefault("busy", []).append(kw),
        on_error=lambda exc: state.setdefault("errors", []).append(exc),
        on_status_link=lambda text, path: state.setdefault("links", []).append(
            (text, path)
        ),
    )


def test_single_export_writes_and_links(tmp_path):
    """A finished render is written and linked in the status line."""
    state: dict[str, list[Any]] = {}
    controller = single_controller(state)
    out = tmp_path / "out.jpg"
    controller._on_exported(str(out), small_jpeg())
    assert out.exists() and out.stat().st_size > 0
    assert state["busy"][-1] == {"busy": False, "status": "Exported."}
    assert state["links"] == [(f"Exported to {out}", str(out))]


def test_single_export_failure_lands_in_the_status(tmp_path, monkeypatch):
    """A write failure resets busy with the error, no link."""
    state: dict[str, list[Any]] = {}
    controller = single_controller(state)

    def explode(*a, **kw):
        raise OSError("read-only")

    monkeypatch.setattr(module, "write_jpeg", explode)
    controller._on_exported(str(tmp_path / "out.jpg"), small_jpeg())
    assert "Export failed" in state["busy"][-1]["status"]
    assert "links" not in state


class FakeSession:
    """Batch-facing session: open and render, optionally foreign."""

    def __init__(self, jpeg, *, foreign=False):
        """Serve jpeg on render; optionally refuse the RAF."""
        self._jpeg = jpeg
        self._foreign = foreign
        self.profile = None

    def open(self, raf_file):
        """Accept the RAF, or refuse it as foreign."""
        if self._foreign:
            raise ForeignRafError("0x2002")

    def render(self, recipe, *, full_resolution):
        """Return the configured camera JPEG."""
        return self._jpeg


def batch_controller(session, settings):
    """A BatchController wired to fakes (dialog paths untouched)."""
    return BatchController(
        parent=None,
        worker=None,
        session=session,
        settings=settings,
        get_paths=list,
        get_recipe=Recipe,
        get_current_raf=lambda: None,
        set_busy=lambda **kw: None,
        on_status=lambda text: None,
        on_error=lambda exc: None,
    )


def tally() -> dict[str, int]:
    """A fresh batch tally."""
    return {"exported": 0, "existing": 0, "foreign": 0, "failed": 0}


def test_batch_passthrough_writes_camera_bytes(tmp_path):
    """No sidecar, no framing: the camera JPEG lands verbatim."""
    jpeg = small_jpeg()
    controller = batch_controller(FakeSession(jpeg), Settings())
    out = tmp_path / "a.jpg"
    counts = tally()
    controller._export_one(
        str(tmp_path / "a.RAF"), out, Recipe(), True, counts
    )
    assert out.read_bytes() == jpeg
    assert counts["exported"] == 1


def test_batch_resize_forces_a_decode(tmp_path):
    """A long-edge limit re-encodes instead of passing bytes through."""
    jpeg = small_jpeg()
    settings = Settings()
    settings.export_max_edge = 4
    controller = batch_controller(FakeSession(jpeg), settings)
    out = tmp_path / "a.jpg"
    controller._export_one(
        str(tmp_path / "a.RAF"), out, Recipe(), True, tally()
    )
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(out))
    assert max(pixbuf.get_width(), pixbuf.get_height()) == 4


def test_batch_credits_stamp_the_passthrough(tmp_path):
    """Artist/copyright reach the EXIF even on the verbatim path."""
    gi.require_version("GExiv2", "0.10")
    from gi.repository import GExiv2

    settings = Settings()
    settings.export_artist = "Jane"
    controller = batch_controller(FakeSession(small_jpeg()), settings)
    out = tmp_path / "a.jpg"
    controller._export_one(
        str(tmp_path / "a.RAF"), out, Recipe(), True, tally()
    )
    meta = GExiv2.Metadata()
    meta.open_path(str(out))
    assert meta.try_get_tag_string("Exif.Image.Artist") == "Jane"


def test_batch_skips_foreign_rafs(tmp_path):
    """A foreign RAF counts as skipped, not failed."""
    controller = batch_controller(
        FakeSession(small_jpeg(), foreign=True), Settings()
    )
    counts = tally()
    controller._export_one(
        str(tmp_path / "a.RAF"), tmp_path / "a.jpg", Recipe(), True, counts
    )
    assert counts["foreign"] == 1
    assert counts["exported"] == 0


def test_batch_write_failures_are_tallied(tmp_path, monkeypatch):
    """An unwritable target counts as failed, no exception escapes."""
    controller = batch_controller(FakeSession(small_jpeg()), Settings())
    counts = tally()
    controller._export_one(
        str(tmp_path / "a.RAF"),
        tmp_path / "missing-dir" / "a.jpg",
        Recipe(),
        True,
        counts,
    )
    assert counts["failed"] == 1
