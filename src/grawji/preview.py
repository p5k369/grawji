"""Preview pipeline - preview vs. full-resolution render.

Camera operations block for seconds, so renders run on a worker thread
and results are handed back to the GTK main loop via ``GLib.idle_add``.
Slider-driven previews are debounced (render on release, show a
spinner). Preview mode uses ``trigger_conversion(full_resolution=False)``
(fast, ignores profile size); export uses ``full_resolution=True``.

This module is a placeholder for the threading glue; the pure mapping
logic lives in :mod:`grawji.recipe` and :mod:`grawji.core`.
"""

from __future__ import annotations
