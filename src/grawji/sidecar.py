"""Per-image sidecar storage.

One sidecar per RAF holds everything grawji knows about that image
beyond the recipe: the crop/rotate geometry and the per-image EV.
The format contract: a top-level "version" plus one key per purpose.
Readers ignore unknown keys, writers preserve them, and "version"
only ever bumps on a break that ignoring keys cannot absorb.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from grawji.crop import CropRotate

SIDECAR_SUFFIX = ".grawji.json"

# The camera-honored EV range (below -2 the engine falls back to 0).
_EV_MIN = -2.0
_EV_MAX = 3.0

_log = logging.getLogger("grawji")


def sidecar_path(raf_path: Path | str) -> Path:
    """The sidecar path for a RAF: the RAF name plus .grawji.json."""
    raf = Path(raf_path)
    return raf.with_name(raf.name + SIDECAR_SUFFIX)


def _read(raf_path: Path | str) -> dict[str, object]:
    """The sidecar's stored dict, or an empty one when absent/broken."""
    try:
        data = json.loads(sidecar_path(raf_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _update(raf_path: Path | str, key: str, value: object | None) -> None:
    """Read-modify-write one sidecar key."""
    path = sidecar_path(raf_path)
    data = _read(raf_path)
    data.pop("version", None)
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    try:
        if data:
            path.write_text(
                json.dumps({"version": 1, **data}, indent=2),
                encoding="utf-8",
            )
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        _log.warning("could not write sidecar %s: %s", path, exc)


def load_crop(raf_path: Path | str) -> CropRotate:
    """The RAF's stored geometry, or the identity when absent."""
    value = _read(raf_path).get("crop")
    if not isinstance(value, dict):
        return CropRotate()
    return CropRotate.from_dict(value)


def save_crop(raf_path: Path | str, crop: CropRotate) -> None:
    """Store the RAF's geometry, dropping the key for the identity."""
    _update(raf_path, "crop", None if crop.is_identity else crop.to_dict())


def edit_flags(raf_path: Path | str) -> tuple[bool, bool]:
    """Whether the sidecar holds an edit.

    The two edits are independent: identity crops and cleared EVs are
    stored as absent keys, so key presence means a real edit.
    """
    data = _read(raf_path)
    return "crop" in data, "exposure" in data


def load_exposure(raf_path: Path | str) -> float | None:
    """The RAF's stored per-image EV, or None when never set."""
    value = _read(raf_path).get("exposure")
    if not isinstance(value, (int, float)):
        return None
    return max(_EV_MIN, min(_EV_MAX, float(value)))


def save_exposure(raf_path: Path | str, exposure: float | None) -> None:
    """Store the RAF's per-image EV (None removes it)."""
    _update(raf_path, "exposure", exposure)
