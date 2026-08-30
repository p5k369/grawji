"""Tests for the per-image sidecar storage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grawji.crop import CropRotate
from grawji.sidecar import (
    load_crop,
    load_exposure,
    save_crop,
    save_exposure,
    sidecar_path,
)


def test_sidecar_round_trip(tmp_path: Path) -> None:
    """Geometry survives a save/load cycle next to the RAF."""
    raf = tmp_path / "DSCF0001.RAF"
    raf.write_bytes(b"raf")
    value = CropRotate(orientation=90, angle=2.5, rect=(0.1, 0.1, 0.7, 0.6))
    save_crop(raf, value)
    assert sidecar_path(raf).name == "DSCF0001.RAF.grawji.json"
    assert load_crop(raf) == value


def test_sidecar_identity_removes_file(tmp_path: Path) -> None:
    """Resetting the geometry deletes the sidecar again."""
    raf = tmp_path / "DSCF0002.RAF"
    raf.write_bytes(b"raf")
    save_crop(raf, CropRotate(orientation=90))
    assert sidecar_path(raf).exists()
    save_crop(raf, CropRotate())
    assert not sidecar_path(raf).exists()


def test_sidecar_exposure(tmp_path: Path) -> None:
    """Per-image EV lives beside the crop; each survives the other."""
    raf = tmp_path / "DSCF0004.RAF"
    raf.write_bytes(b"raf")
    assert load_exposure(raf) is None
    save_exposure(raf, 1 + 2 / 3)
    save_crop(raf, CropRotate(orientation=90))
    assert load_exposure(raf) == pytest.approx(1 + 2 / 3)
    assert load_crop(raf).orientation == 90
    # Resetting the crop keeps the EV, so the file stays.
    save_crop(raf, CropRotate())
    assert sidecar_path(raf).exists()
    assert load_exposure(raf) == pytest.approx(1 + 2 / 3)
    assert load_crop(raf) == CropRotate()
    # Removing the EV as well deletes the whole sidecar.
    save_exposure(raf, None)
    assert not sidecar_path(raf).exists()


def test_sidecar_exposure_sanitized(tmp_path: Path) -> None:
    """Stored EV is clamped to the camera-honored range or ignored."""
    raf = tmp_path / "DSCF0005.RAF"
    raf.write_bytes(b"raf")
    sidecar_path(raf).write_text(
        json.dumps({"version": 1, "exposure": 9.0}), encoding="utf-8"
    )
    assert load_exposure(raf) == 3.0
    sidecar_path(raf).write_text(
        json.dumps({"version": 1, "exposure": "bad"}), encoding="utf-8"
    )
    assert load_exposure(raf) is None


def test_sidecar_preserves_unknown_keys(tmp_path: Path) -> None:
    """Keys from future versions survive this version's writes."""
    raf = tmp_path / "DSCF0006.RAF"
    raf.write_bytes(b"raf")
    sidecar_path(raf).write_text(
        json.dumps({"version": 1, "rating": 5}), encoding="utf-8"
    )
    save_exposure(raf, 0.5)
    save_crop(raf, CropRotate(orientation=180))
    data = json.loads(sidecar_path(raf).read_text(encoding="utf-8"))
    assert data["rating"] == 5
    assert data["exposure"] == 0.5
    assert data["version"] == 1
    # An identity crop and no EV still keep the file: rating remains.
    save_crop(raf, CropRotate())
    save_exposure(raf, None)
    assert sidecar_path(raf).exists()
    data = json.loads(sidecar_path(raf).read_text(encoding="utf-8"))
    assert data == {"version": 1, "rating": 5}


def test_sidecar_missing_or_corrupt(tmp_path: Path) -> None:
    """Absent or unreadable sidecars fall back to the identity."""
    raf = tmp_path / "DSCF0003.RAF"
    assert load_crop(raf) == CropRotate()
    sidecar_path(raf).write_text("not json", encoding="utf-8")
    assert load_crop(raf) == CropRotate()
    sidecar_path(raf).write_text(json.dumps({"crop": 5}), encoding="utf-8")
    assert load_crop(raf) == CropRotate()
