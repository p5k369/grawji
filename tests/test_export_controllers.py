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
import grawji.imaging.export as module_export
from grawji.camera.core import ForeignRafError
from grawji.controllers.exports import (
    BatchController,
    SingleExportController,
)
from grawji.crop import CropRotate
from grawji.recipe import Recipe
from grawji.settings import Settings


def small_jpeg() -> bytes:
    """A tiny valid camera-JPEG stand-in."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 6)
    pixbuf.fill(0x808080FF)
    ok, data = pixbuf.save_to_bufferv("jpeg", [], [])
    assert ok
    return bytes(data)


def single_controller(state, settings=None, identity=False, jpeg=None):
    """A SingleExportController over recording fakes."""
    return SingleExportController(
        parent=None,
        worker=None,
        session=FakeSession(jpeg if jpeg is not None else small_jpeg()),
        settings=settings or Settings(),
        save_settings=lambda: None,
        get_recipe=Recipe,
        get_provenance=lambda: "",
        get_current_raf=lambda: None,
        get_crop=CropRotate,
        base_identity=lambda: identity,
        set_busy=lambda **kw: state.setdefault("busy", []).append(kw),
        on_error=lambda exc: state.setdefault("errors", []).append(exc),
        on_status_link=lambda text, path: state.setdefault("links", []).append(
            (text, path)
        ),
    )


def export_once(controller, path, *, identity=False, crop=None):
    """Run the worker half of an export, then report it as the app does."""
    job = module._ExportJob(
        path=str(path),
        recipe=Recipe(),
        crop=crop or CropRotate(),
        comment="",
        wanted=module.export_format(controller._settings),
        identity=identity,
    )
    written = controller._render_and_write(job)
    controller._on_written(written)
    return written


def test_single_export_writes_and_links(tmp_path):
    """A finished render is written and linked in the status line."""
    state: dict[str, list[Any]] = {}
    controller = single_controller(state)
    out = tmp_path / "out.jpg"
    export_once(controller, out)
    assert out.exists() and out.stat().st_size > 0
    assert state["busy"][-1] == {"busy": False, "status": "Exported."}
    assert state["links"] == [(f"Exported to {out}", str(out))]


def test_single_export_streams_untouched_camera_bytes(tmp_path):
    """No geometry, framing or resize writes the camera bytes as-is."""
    state: dict[str, list[Any]] = {}
    jpeg = small_jpeg()
    controller = single_controller(state, identity=True, jpeg=jpeg)
    out = tmp_path / "out.jpg"
    export_once(controller, out, identity=True)
    assert out.read_bytes() == jpeg
    assert state["busy"][-1] == {"busy": False, "status": "Exported."}


def test_single_export_geometry_forces_a_reencode(tmp_path, monkeypatch):
    """Live crop/rotation routes through write_jpeg, not passthrough."""
    state: dict[str, list[Any]] = {}
    controller = single_controller(state, identity=False)
    calls = []
    monkeypatch.setattr(
        module, "write_jpeg", lambda *a, **kw: calls.append(kw)
    )
    export_once(controller, tmp_path / "out.jpg", identity=False)
    assert len(calls) == 1


def test_single_export_resize_forces_a_reencode(tmp_path, monkeypatch):
    """An active size limit disables the passthrough."""
    state: dict[str, list[Any]] = {}
    settings = Settings()
    settings.export_max_edge = 100
    controller = single_controller(state, settings=settings, identity=True)
    calls = []
    monkeypatch.setattr(
        module, "write_jpeg", lambda *a, **kw: calls.append(kw)
    )
    export_once(controller, tmp_path / "out.jpg", identity=True)
    assert len(calls) == 1


def test_single_export_failure_lands_in_the_status(tmp_path, monkeypatch):
    """A write failure resets busy with the error, no link."""
    state: dict[str, list[Any]] = {}
    controller = single_controller(state)

    def explode(*a, **kw):
        raise OSError("read-only")

    monkeypatch.setattr(module, "write_jpeg", explode)
    with pytest.raises(OSError, match="read-only"):
        export_once(controller, tmp_path / "out.jpg")
    assert "links" not in state


def test_export_format_falls_back_without_the_tool(monkeypatch):
    """A format whose tool is missing degrades to plain JPEG."""
    settings = Settings()
    settings.export_format = "jxl"
    monkeypatch.setattr(module_export.shutil, "which", lambda name: None)
    assert module_export.export_format(settings) == "jpeg"
    monkeypatch.setattr(
        module_export.shutil, "which", lambda name: "/usr/bin/cjxl"
    )
    assert module_export.export_format(settings) == "jxl"
    settings.export_format = "heif"
    monkeypatch.setattr(module_export.heif_codec, "available", lambda: False)
    assert module_export.export_format(settings) == "jpeg"
    monkeypatch.setattr(module_export.heif_codec, "available", lambda: True)
    assert module_export.export_format(settings) == "heif"


def test_camera_file_type_only_changes_for_heif():
    """Only HEIF makes the camera render something other than JPEG."""
    assert module_export.camera_file_type("jpeg") == "jpeg"
    assert module_export.camera_file_type("jxl") == "jpeg"
    assert module_export.camera_file_type("heif") == "heif"


def test_delivered_format_follows_the_bytes():
    """A body that ignores the HEIF request is caught by the magic."""
    heif_bytes = b"\x00\x00\x00\x18ftypheic" + bytes(16)
    assert module_export.delivered_format(heif_bytes, "heif") == "heif"
    assert module_export.delivered_format(small_jpeg(), "heif") == "jpeg"
    assert module_export.delivered_format(small_jpeg(), "jxl") == "jxl"


def test_corrected_path_renames_a_fallback():
    """The file is named for what arrived, not what was asked for."""
    assert module_export.corrected_path("/x/a.heif", "jpeg") == "/x/a.jpg"
    assert module_export.corrected_path("/x/a.heif", "heif") == "/x/a.heif"


def test_export_basename_extension_follows_the_format():
    """The default name carries the format's extension."""
    assert module_export.export_basename("a/b.RAF") == "b.jpg"
    assert module_export.export_basename("a/b.RAF", fmt="jxl") == "b.jxl"
    assert module_export.export_basename("a/b.RAF", fmt="heif") == "b.heif"


def test_single_export_jxl_repacks_the_passthrough(tmp_path, monkeypatch):
    """JXL mode routes the untouched camera bytes through the repack."""
    state: dict[str, list[Any]] = {}
    settings = Settings()
    settings.export_format = "jxl"
    controller = single_controller(state, settings=settings, identity=True)
    repacked = []
    monkeypatch.setattr(
        module_export.shutil, "which", lambda name: "/usr/bin/cjxl"
    )
    monkeypatch.setattr(
        module_export,
        "repack_jxl",
        lambda jpeg, path: repacked.append((jpeg, path)),
    )
    jpeg = small_jpeg()
    out = tmp_path / "out.jxl"
    export_once(controller, out, identity=True)
    assert repacked == [(jpeg, str(out))]
    assert state["busy"][-1] == {"busy": False, "status": "Exported."}


@pytest.mark.skipif(
    not module_export.jxl_available(), reason="cjxl not installed"
)
def test_repack_jxl_writes_a_jxl_container(tmp_path):
    """The real cjxl repack produces a JPEG XL file."""
    out = tmp_path / "out.jxl"
    module_export.repack_jxl(small_jpeg(), str(out))
    data = out.read_bytes()
    assert data.startswith(b"\xff\x0a") or data[4:12] == b"JXL \r\n\x87\n"


def test_repack_jxl_without_cjxl_raises(tmp_path, monkeypatch):
    """A missing tool is a clean OSError, not a crash."""
    monkeypatch.setattr(module_export.shutil, "which", lambda name: None)
    with pytest.raises(OSError, match="cjxl"):
        module_export.repack_jxl(b"x", str(tmp_path / "out.jxl"))


class FakeSession:
    """Batch-facing session: open and render, optionally foreign."""

    def __init__(self, jpeg, *, foreign=False):
        """Serve jpeg on render; optionally refuse the RAF."""
        self._jpeg = jpeg
        self._foreign = foreign
        self.profile = None
        self.file_types = []
        self.recipes = []

    def open(self, raf_file):
        """Accept the RAF, or refuse it as foreign."""
        if self._foreign:
            raise ForeignRafError("0x2002")

    def render(self, recipe, *, full_resolution, file_type="jpeg"):
        """Return the configured camera JPEG."""
        self.file_types.append(file_type)
        self.recipes.append(recipe)
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
        get_provenance=lambda: "",
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
        str(tmp_path / "a.RAF"), out, Recipe(), "", True, counts
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
        str(tmp_path / "a.RAF"), out, Recipe(), "prov", True, tally()
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
        str(tmp_path / "a.RAF"), out, Recipe(), "prov", True, tally()
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
        str(tmp_path / "a.RAF"),
        tmp_path / "a.jpg",
        Recipe(),
        "prov",
        True,
        counts,
    )
    assert counts["foreign"] == 1
    assert counts["exported"] == 0


def test_batch_export_stamps_provenance(tmp_path):
    """The provenance comment lands in the exported file's EXIF."""
    gexiv2 = pytest.importorskip("gi.repository.GExiv2")
    controller = batch_controller(FakeSession(small_jpeg()), Settings())
    out = tmp_path / "a.jpg"
    counts = tally()
    controller._export_one(
        str(tmp_path / "a.RAF"),
        out,
        Recipe(),
        "grawji recipe: Test",
        True,
        counts,
    )
    assert counts["exported"] == 1
    meta = gexiv2.Metadata()
    meta.open_path(str(out))
    comment = meta.try_get_tag_string("Exif.Photo.UserComment") or ""
    assert "grawji recipe: Test" in comment


def test_batch_write_failures_are_tallied(tmp_path, monkeypatch):
    """An unwritable target counts as failed, no exception escapes."""
    controller = batch_controller(FakeSession(small_jpeg()), Settings())
    counts = tally()
    controller._export_one(
        str(tmp_path / "a.RAF"),
        tmp_path / "missing-dir" / "a.jpg",
        Recipe(),
        "prov",
        True,
        counts,
    )
    assert counts["failed"] == 1


def test_heif_export_asks_the_camera_for_heif(tmp_path, monkeypatch):
    """A HEIF export renders as HEIF and passes the bytes through."""
    settings = Settings()
    settings.export_format = "heif"
    monkeypatch.setattr(module_export.heif_codec, "available", lambda: True)
    camera_heif = b"\x00\x00\x00\x18ftypheic" + bytes(64)
    session = FakeSession(camera_heif)
    controller = batch_controller(session, settings)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    controller._export_one(
        str(tmp_path / "a.RAF"),
        out_dir / "a.heif",
        Recipe(),
        "",
        False,
        {"exported": 0, "failed": 0},
    )
    assert session.file_types == ["heif"]
    assert (out_dir / "a.heif").read_bytes() == camera_heif


def test_heif_fallback_to_jpeg_renames_the_export(tmp_path, monkeypatch):
    """A body without HEIF sends JPEG, so the export becomes a .jpg."""
    settings = Settings()
    settings.export_format = "heif"
    monkeypatch.setattr(module_export.heif_codec, "available", lambda: True)
    jpeg = small_jpeg()
    session = FakeSession(jpeg)
    controller = batch_controller(session, settings)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    tally = {"exported": 0, "failed": 0}
    controller._export_one(
        str(tmp_path / "a.RAF"),
        out_dir / "a.heif",
        Recipe(),
        "",
        False,
        tally,
    )
    assert (out_dir / "a.jpg").read_bytes() == jpeg
    assert not (out_dir / "a.heif").exists()
    assert tally["exported"] == 1


def test_heif_drops_clarity_and_says_so(tmp_path, monkeypatch):
    """The camera greys Clarity out for HEIF, so the export does too."""
    settings = Settings()
    settings.export_format = "heif"
    monkeypatch.setattr(module_export.heif_codec, "available", lambda: True)
    camera_heif = b"\x00\x00\x00\x18ftypheic" + bytes(64)
    state: dict[str, list[Any]] = {}
    controller = single_controller(
        state, settings=settings, identity=True, jpeg=camera_heif
    )
    monkeypatch.setattr(module_export, "_check_complete", lambda *a: None)
    recipe, dropped = module_export.recipe_for_format(
        "heif", Recipe(clarity=3)
    )
    job = module._ExportJob(
        path=str(tmp_path / "out.heif"),
        recipe=recipe,
        crop=CropRotate(),
        comment="",
        wanted="heif",
        identity=True,
        dropped=dropped,
    )
    controller._on_written(controller._render_and_write(job))
    assert controller._session.recipes[0].clarity == 0
    assert "Clarity" in state["busy"][-1]["status"]


def test_recipe_for_format_only_touches_heif_with_clarity():
    """Other formats keep Clarity, and a zero Clarity changes nothing."""
    assert module_export.recipe_for_format("heif", Recipe(clarity=-1)) == (
        Recipe(clarity=0),
        "Clarity is not available in HEIF, exported without it.",
    )
    for fmt in ("jpeg", "jxl"):
        recipe, note = module_export.recipe_for_format(fmt, Recipe(clarity=3))
        assert recipe.clarity == 3
        assert note == ""
    recipe, note = module_export.recipe_for_format("heif", Recipe())
    assert recipe.clarity == 0
    assert note == ""
