"""Persistent application settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

FROM_IMAGE = "__from_image__"
FROM_IMAGE_LABEL = "From image"


def config_dir() -> Path:
    """Return grawji's config directory (honors XDG_CONFIG_HOME)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "grawji"


def settings_path() -> Path:
    """Return the path to the settings JSON file."""
    return config_dir() / "settings.json"


def cache_dir() -> Path:
    """Return grawji's cache directory (honors XDG_CACHE_HOME)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "grawji"


@dataclass
class Settings:
    """User-configurable application settings.

    Attributes:
        open_recipe: The sticky recipe selection applied to each opened
            image. FROM_IMAGE loads each image's own in-camera recipe.
            Any other value is a saved recipe name applied to every image.
        sidebar_width: Width of the left side panel in pixels; drag the
            pane handle to 0 to collapse it (remembered across runs).
        canvas_background: Preview background CSS class ("" = themed).
        last_folder: Last folder opened in the filmstrip (re-scanned on
            startup); empty means none.
        window_width: Last window width in pixels (0 = use the default).
        window_height: Last window height in pixels (0 = use the default).
        jpeg_quality: JPEG quality for exports, 1 to 100.
        batch_overwrite: On batch export, re-export images whose JPEG
            already exists in the target folder. When False, such images
            are skipped so an interrupted batch can be resumed cheaply.
        wb_grid_tint: When True, tint each white-balance shift grid cell
            with the color it nudges the image toward.
        nav_glide_speed: Filmstrip scroll speed while an arrow is held,
            in pixels per second.
        bookmarks: Bookmarked folder paths, shown at the top of the
            folder tree in the order they were added.
        color_scheme: UI theme: "default" follows the desktop, "light" or
            "dark" forces that scheme.
        show_histogram: Whether the histogram overlay is shown on the
            preview.
        last_export_dir: Folder of the most recent export.
        the export dialogs open here. Empty means none.
        expanded_folders: Folder-tree paths that were expanded, restored on
            startup so the tree reopens as it was left.
        last_image: Path of the image selected in the filmstrip, reselected
            on startup when it is still present. Empty means none.
        crop_guides: Composition guides shown in the crop editor
            ("None", "Thirds", "Grid", ...).
        export_border_enabled: Whether exports get a framing border.
        export_border_percent: Width of the border as a percentage of
            the image's longer edge (the minimum frame on every side).
        export_border_color: The border color as an RRGGBB hex string.
        export_border_aspect: Pad the framed canvas to this "W:H"
            aspect ratio with the border color.
    """

    open_recipe: str = FROM_IMAGE
    sidebar_width: int = 240
    canvas_background: str = ""
    last_folder: str = ""
    window_width: int = 0
    window_height: int = 0
    jpeg_quality: int = 95
    batch_overwrite: bool = False
    wb_grid_tint: bool = True
    nav_glide_speed: int = 600
    bookmarks: list[str] = field(default_factory=list)
    color_scheme: str = "default"
    show_histogram: bool = False
    last_export_dir: str = ""
    expanded_folders: list[str] = field(default_factory=list)
    last_image: str = ""
    crop_guides: str = "Thirds"
    export_border_enabled: bool = False
    export_border_percent: float = 3.0
    export_border_color: str = "#ffffff"
    export_border_aspect: str = "None"

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
