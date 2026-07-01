"""Persistent application settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def config_dir() -> Path:
    """Return grawji's config directory (honours XDG_CONFIG_HOME)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "grawji"


def settings_path() -> Path:
    """Return the path to the settings JSON file."""
    return config_dir() / "settings.json"


@dataclass
class Settings:
    """User-configurable application settings.

    Attributes:
        load_recipe_from_image: When True, selecting an image loads
            its own in-camera recipe into the controls. When False,
            the current recipe is kept and applied to the new image.
        sidebar_width: Width of the left side panel in pixels; drag the
            pane handle to 0 to collapse it (remembered across runs).
        canvas_background: Preview background CSS class ("" = themed).
        last_folder: Last folder opened in the filmstrip (re-scanned on
            startup); empty means none.
        window_width: Last window width in pixels (0 = use the default).
        window_height: Last window height in pixels (0 = use the default).
        jpeg_quality: JPEG quality for exports, 1 to 100.
        batch_skip_foreign: On batch export, skip RAFs shot by a different
            camera body (which the connected camera cannot convert) and
            carry on, instead of stopping the whole batch.
    """

    load_recipe_from_image: bool = True
    sidebar_width: int = 240
    canvas_background: str = ""
    last_folder: str = ""
    window_width: int = 0
    window_height: int = 0
    jpeg_quality: int = 95
    batch_skip_foreign: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a plain dict for JSON storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Settings:
        """Build settings from a stored dict, ignoring unknown keys."""
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)  # type: ignore[arg-type]


def load_settings(path: Path) -> Settings:
    """Load settings from path, returning defaults if unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    return Settings.from_dict(data)


def save_settings(settings: Settings, path: Path) -> None:
    """Write settings to path as JSON, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
