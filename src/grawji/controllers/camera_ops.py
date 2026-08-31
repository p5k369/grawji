"""Controller for camera side jobs: bank transfers and name reads."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib

from grawji.camera.core import CameraSession


class CameraOpsController:
    """Bank reads and recipe-to-camera transfers for the window."""

    def __init__(self, session: CameraSession) -> None:
        """Bind the controller to the camera session."""
        self._session = session

    def load_bank_names(self, on_loaded: Callable[[list[str]], None]) -> None:
        """Read current bank names on a worker thread."""

        def work() -> None:
            try:
                names = self._session.read_bank_names()
            except Exception:
                names = []
            GLib.idle_add(on_loaded, names)

        threading.Thread(target=work, daemon=True).start()

    def run_bank_transfer(
        self,
        recipes: dict[Any, Any],
        names: dict[Any, Any],
        fs_recipes: dict[Any, Any],
        on_done: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Transfer recipes to the camera's banks and FS dial on a worker."""

        def work() -> None:
            written: list[str] = []
            dropped: set[str] = set()
            model = ""
            try:
                if recipes or names:
                    result = self._session.transfer_bank_recipes(
                        recipes, names
                    )
                    model = result.model or model
                    written += [f"C{i + 1}" for i in result.slots]
                    dropped |= {
                        f for fs in result.dropped.values() for f in fs
                    }
                if fs_recipes:
                    result = self._session.transfer_fs_recipes(fs_recipes)
                    model = result.model or model
                    written += [f"FS{i + 1}" for i in result.slots]
                    dropped |= {
                        f for fs in result.dropped.values() for f in fs
                    }
            except Exception as exc:
                GLib.idle_add(on_error, f"Bank transfer failed: {exc}")
                return
            slots = ", ".join(written)
            message = (
                f"Wrote recipes to {slots} on the {model}. "
                "The live preview session was closed."
            )
            if dropped:
                message += (
                    " Dropped unsupported: " + ", ".join(sorted(dropped)) + "."
                )
            GLib.idle_add(on_done, message)

        threading.Thread(target=work, daemon=True).start()
