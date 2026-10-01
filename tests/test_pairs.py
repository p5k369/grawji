"""Tests for finding the camera JPEG beside a RAF."""

from __future__ import annotations

from pathlib import Path

from grawji.pairs import is_jpeg_name, jpegs_by_stem, paired_jpeg


def test_the_jpeg_with_the_same_stem_is_the_pair(tmp_path: Path) -> None:
    """Either spelling a card reader leaves behind is found."""
    raf = tmp_path / "DSCF0001.RAF"
    raf.write_bytes(b"raf")
    assert paired_jpeg(raf) is None
    (tmp_path / "DSCF0001.jpg").write_bytes(b"jpg")
    assert paired_jpeg(raf) == tmp_path / "DSCF0001.jpg"


def test_a_jpeg_of_another_shot_is_no_pair(tmp_path: Path) -> None:
    """Only the exact stem counts."""
    raf = tmp_path / "DSCF0001.RAF"
    raf.write_bytes(b"raf")
    (tmp_path / "DSCF0001_edit.JPG").write_bytes(b"jpg")
    assert paired_jpeg(raf) is None


def test_a_listing_maps_stems_to_jpegs() -> None:
    """One listing serves a whole folder, non-JPEGs are skipped."""
    names = ["a.RAF", "a.JPG", "b.jpeg", "c.txt", "c.RAF"]
    assert jpegs_by_stem(names) == {"a": "a.JPG", "b": "b.jpeg"}


def test_the_listing_prefers_the_same_spelling_as_the_lookup() -> None:
    """Two spellings of one stem resolve like paired_jpeg would."""
    assert jpegs_by_stem(["a.jpg", "a.JPG"]) == {"a": "a.JPG"}


def test_jpeg_names() -> None:
    """The folder watcher wakes up for JPEGs too."""
    assert is_jpeg_name("DSCF0001.JPG")
    assert is_jpeg_name("x.jpeg")
    assert not is_jpeg_name("DSCF0001.RAF")
