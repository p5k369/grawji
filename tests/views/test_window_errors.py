"""Camera errors that are normal use stay out of dialogs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grawji.camera.core import ForeignRafError

pytestmark = pytest.mark.gui


def test_foreign_raf_is_a_status_line_not_a_dialog(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Browsing another body's shots never pops a dialog per image."""
    from grawji.views import window as window_module

    monkeypatch.setattr(
        window_module.raf, "header_model", lambda _path: "X-T3"
    )
    monkeypatch.setattr(
        window_module.raf, "output_size", lambda _path: (6240, 4160)
    )
    monkeypatch.setattr(type(window._session), "model", "X-E5")
    toasts: list[Any] = []
    monkeypatch.setattr(window.toast_overlay, "add_toast", toasts.append)
    window._raf_path = Path("/nowhere/shot.RAF")
    window._on_error(ForeignRafError("no carrier"))
    assert window._error_showing is False
    assert "X-T3" in window.preview_view.status.get_label()
    assert len(toasts) == 1
    assert "X-E5 needs a carrier" in toasts[0].get_title()
    window._on_error(ForeignRafError("no carrier"))
    assert len(toasts) == 1


def test_donor_too_big_for_the_body_gets_no_carrier_offer(
    window: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An X-T3 cannot carry an X-E5 picture, so no carrier is suggested."""
    from grawji.views import window as window_module

    monkeypatch.setattr(
        window_module.raf, "header_model", lambda _path: "X-E5"
    )
    monkeypatch.setattr(
        window_module.raf, "output_size", lambda _path: (7728, 5152)
    )
    monkeypatch.setattr(type(window._session), "model", "X-T3")
    toasts: list[Any] = []
    monkeypatch.setattr(window.toast_overlay, "add_toast", toasts.append)
    window._raf_path = Path("/nowhere/shot.RAF")
    window._on_error(ForeignRafError("no carrier"))
    assert toasts[0].get_title() == "X-T3 cannot develop X-E5 shots."
    assert toasts[0].get_button_label() is None
