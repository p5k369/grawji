"""Main-loop scheduling that stays ahead of the repaint."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib  # noqa: E402

# Anything that schedules a callback on the main loop. Workers take it
# as a parameter, so tests can run the callback inline instead.
Dispatch = Callable[[Callable[[], None]], Any]


def call(callback: Callable[..., Any], *args: Any) -> int:
    """Run callback(*args) on the main loop before the next repaint."""
    return int(GLib.idle_add(callback, *args, priority=GLib.PRIORITY_DEFAULT))
