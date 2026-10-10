"""A RAW+JPEG shot developed from its camera file."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from grawji import sidecar
from grawji.camera import camera_info
from grawji.pairs import Source

pytestmark = pytest.mark.gui


def _settle(seconds: float) -> None:
    """Let the load delay and the decode thread run."""
    from tests.gui_support import pump

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump()
        time.sleep(0.01)


def _pair(folder: Path) -> tuple[Path, Path]:
    """A RAF stand-in and a real little camera JPEG beside it."""
    from gi.repository import GdkPixbuf

    raf = folder / "DSCF0001.RAF"
    raf.write_bytes(b"not a real raf")
    jpeg = folder / "DSCF0001.JPG"
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 12, 8)
    pixbuf.fill(0x808080FF)
    pixbuf.savev(str(jpeg), "jpeg", [], [])
    return raf, jpeg


def test_a_camera_file_opens_without_the_camera(
    window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No RAF goes to the camera until the shot switches to RAW."""
    raf, jpeg = _pair(tmp_path)
    sidecar.save_source(raf, Source.CAMERA)
    opened: list[str] = []
    window._worker.open = lambda path, **_kw: opened.append(path)
    window.set_default_size(900, 700)
    window.present()
    window._scan_folder(str(tmp_path))
    _settle(0.3)
    window._filmstrip.select_path(str(raf))
    _settle(0.6)
    assert opened == []
    assert window._camera_file_of(raf) == str(jpeg)
    assert window.preview_view.source_switch.get_visible()
    assert window.export_button.get_sensitive()

    monkeypatch.setattr(camera_info, "detect_camera", lambda: "X-E5")
    window._toggle_source()
    _settle(0.6)
    assert opened == [str(raf)]
    assert sidecar.summary(raf).source is Source.RAW
    assert window._camera_file_of(raf) is None


def test_a_lone_raf_offers_no_switch(window: Any, tmp_path: Path) -> None:
    """Without a camera file the switch stays hidden."""
    raf = tmp_path / "DSCF0002.RAF"
    raf.write_bytes(b"not a real raf")
    window._worker.open = lambda *_a, **_kw: None
    window.present()
    window._scan_folder(str(tmp_path))
    _settle(0.3)
    window._filmstrip.select_path(str(raf))
    _settle(0.3)
    assert not window.preview_view.source_switch.get_visible()
    window._toggle_source()
    assert sidecar.summary(raf).source is None


def test_without_a_camera_a_raf_browses_quietly(
    window: Any, tmp_path: Path
) -> None:
    """No camera on USB means no open and no error dialog."""
    raf = tmp_path / "DSCF0003.RAF"
    raf.write_bytes(b"not a real raf")
    opened: list[str] = []
    window._worker.open = lambda path, **_kw: opened.append(path)
    window.present()
    window._scan_folder(str(tmp_path))
    _settle(0.3)
    window._filmstrip.select_path(str(raf))
    _settle(0.3)
    assert opened == []
    assert not window._error_showing
    assert "No camera" in window.preview_view.status.get_label()


def test_with_a_camera_a_raf_goes_to_it(
    window: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A camera on USB gets the selected RAF."""
    monkeypatch.setattr(camera_info, "detect_camera", lambda: "X-E5")
    raf = tmp_path / "DSCF0004.RAF"
    raf.write_bytes(b"not a real raf")
    opened: list[str] = []
    window._worker.open = lambda path, **_kw: opened.append(path)
    window.present()
    window._scan_folder(str(tmp_path))
    _settle(0.3)
    window._filmstrip.select_path(str(raf))
    _settle(0.3)
    assert opened == [str(raf)]
