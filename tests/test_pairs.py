"""Tests for finding the camera JPEG beside a RAF."""

from __future__ import annotations

from pathlib import Path

from grawji.pairs import (
    Source,
    companion_label,
    companion_of,
    companions_by_stem,
    is_companion_name,
    source_of,
)


def test_the_jpeg_with_the_same_stem_is_the_pair(tmp_path: Path) -> None:
    """Either spelling a card reader leaves behind is found."""
    raf = tmp_path / "DSCF0001.RAF"
    raf.write_bytes(b"raf")
    assert companion_of(raf) is None
    (tmp_path / "DSCF0001.jpg").write_bytes(b"jpg")
    assert companion_of(raf) == tmp_path / "DSCF0001.jpg"


def test_a_jpeg_of_another_shot_is_no_pair(tmp_path: Path) -> None:
    """Only the exact stem counts."""
    raf = tmp_path / "DSCF0001.RAF"
    raf.write_bytes(b"raf")
    (tmp_path / "DSCF0001_edit.JPG").write_bytes(b"jpg")
    assert companion_of(raf) is None


def test_a_listing_maps_stems_to_jpegs() -> None:
    """One listing serves a whole folder, non-JPEGs are skipped."""
    names = ["a.RAF", "a.JPG", "b.jpeg", "c.txt", "c.RAF"]
    assert companions_by_stem(names) == {"a": "a.JPG", "b": "b.jpeg"}


def test_the_listing_prefers_the_same_spelling_as_the_lookup() -> None:
    """Two spellings of one stem resolve like companion_of would."""
    assert companions_by_stem(["a.jpg", "a.JPG"]) == {"a": "a.JPG"}


def test_jpeg_names() -> None:
    """The folder watcher wakes up for JPEGs too."""
    assert is_companion_name("DSCF0001.JPG")
    assert is_companion_name("x.jpeg")
    assert not is_companion_name("DSCF0001.RAF")


def test_a_stored_source_reads_back_or_is_dropped() -> None:
    """Unknown or missing values mean the shot was never switched."""
    assert source_of("camera") is Source.CAMERA
    assert source_of("raw") is Source.RAW
    assert source_of("tiff") is None
    assert source_of(None) is None


def test_a_camera_file_goes_by_its_suffix() -> None:
    """The switch and the card name the file the camera wrote."""
    assert companion_label("/a/DSCF0001.JPG") == "JPG"
    assert companion_label("/a/DSCF0001.hif") == "HIF"
