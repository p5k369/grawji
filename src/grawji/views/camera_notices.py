"""What the window tells the user when the camera cannot do something."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from grawji import raf
from grawji.camera import camera_info
from grawji.camera.capabilities import can_carry, is_known_model
from grawji.camera.core import CameraSession, ForeignRafError
from grawji.settings import Settings
from grawji.transplant import opener

_REPORT_URL = (
    "https://github.com/p5k369/grawji/issues/new?template=new-body-report.yml"
)


@dataclass(frozen=True)
class NoticeHost:
    """What the notices need from the window.

    Attributes:
        current_raf: The image the window shows.
        set_busy: Takes the busy flag and the status line.
        abort_batch: Stops a batch export that a failure interrupted.
        add_toast: Shows a toast.
        manage_carriers: Opens the place where carriers are set up.
    """

    current_raf: Callable[[], Path | None]
    set_busy: Callable[[bool, str], None]
    abort_batch: Callable[[], None]
    add_toast: Callable[[Adw.Toast], None]
    manage_carriers: Callable[[], None]


class CameraNotices:
    """Turns camera failures into a dialog, a toast or a status line."""

    def __init__(
        self,
        parent: Gtk.Window,
        session: CameraSession,
        settings: Settings,
        host: NoticeHost,
    ) -> None:
        """Bind the notices to the window and the camera session."""
        self._parent = parent
        self._session = session
        self._settings = settings
        self._host = host
        self._unverified: set[str] = set()
        self._foreign_warned: set[str] = set()
        self.showing = False

    def report(self, exc: Exception) -> None:
        """Surface a failed camera operation and reset the busy state."""
        logging.getLogger("grawji").warning("camera operation failed: %s", exc)
        self._host.set_busy(False, "Error.")
        # A camera failure aborts any batch mid-flight, unstick its dialog.
        self._host.abort_batch()
        if isinstance(exc, ForeignRafError):
            named = self._foreign_model()
            self._host.set_busy(
                False, f"Shot on {named}, showing the in-camera JPEG."
            )
            self._warn_foreign_once(named)
            return
        # A wedged camera fails every queued render in turn; show one dialog.
        if self.showing:
            return
        message, detail = camera_info.error_text(exc)
        self.showing = True
        alert = Gtk.AlertDialog()
        alert.set_message(message)
        alert.set_detail(detail)
        alert.set_buttons(["Close"])
        alert.choose(self._parent, None, self._on_dismissed)

    def report_quietly(self, exc: Exception) -> None:
        """Reset the busy state after a failure nobody asked about."""
        logging.getLogger("grawji").info("auto reconnect failed: %s", exc)
        self._host.set_busy(False, "")

    def notify_unverified(self, model: str | None) -> None:
        """Toast once per session when the body is not in the table."""
        if model is None or is_known_model(model):
            return
        if model in self._unverified:
            return
        self._unverified.add(model)
        toast = Adw.Toast.new(
            f"The {model} is not in grawji's capability table yet - "
            "showing the safe baseline. Please report your body!"
        )
        toast.set_timeout(0)  # stays until dismissed, it is actionable
        toast.set_button_label("Report…")
        toast.connect("button-clicked", self._on_report_body)
        self._host.add_toast(toast)

    def _on_report_body(self, _toast: Any) -> None:
        """Open the new-body issue template in the browser."""
        Gtk.UriLauncher.new(_REPORT_URL).launch(self._parent, None, None)

    def _foreign_model(self) -> str:
        """The body named in the current RAF's header."""
        path = self._host.current_raf()
        named = raf.header_model(path) if path else None
        return named or "another body"

    def _warn_foreign_once(self, named: str) -> None:
        """Toast the first RAF of another body per session."""
        if named in self._foreign_warned:
            return
        self._foreign_warned.add(named)
        model = self._session.model
        path = self._host.current_raf()
        size = raf.output_size(path) if path else None
        fits = can_carry(model, named, size) if size else None
        if (
            model
            and fits is not False
            and not opener.carriers_for(self._settings, model)
        ):
            toast = Adw.Toast.new(
                f"The {model} needs a carrier to develop {named} shots."
            )
            toast.set_button_label("Set up…")
            toast.connect(
                "button-clicked", lambda _t: self._host.manage_carriers()
            )
        else:
            body = model or "The camera"
            toast = Adw.Toast.new(f"{body} cannot develop {named} shots.")
        self._host.add_toast(toast)

    def _on_dismissed(self, dialog: Any, result: Any) -> None:
        """Allow the next error dialog once this one is closed."""
        with contextlib.suppress(GLib.Error):
            dialog.choose_finish(result)
        self.showing = False
