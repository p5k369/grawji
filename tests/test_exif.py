"""Tests for EXIF display formatting."""

from grawji.exif import format_exif


def test_formats_known_fields():
    """Rationals and ISO are formatted into human-readable values."""
    rows = format_exif(
        {
            "Exif.Image.Model": "X-T3",
            "Exif.Photo.FocalLength": "3500/100",
            "Exif.Photo.FNumber": "280/100",
            "Exif.Photo.ExposureTime": "10/3400",
            "Exif.Photo.ISOSpeedRatings": "320",
        }
    )
    assert rows == [
        ("Camera", "X-T3"),
        ("Focal length", "35 mm"),
        ("Aperture", "f/2.8"),
        ("Shutter", "1/340 s"),
        ("ISO", "ISO 320"),
    ]


def test_slow_shutter_shown_in_seconds():
    """A shutter speed >= 1s is shown in seconds, not as a fraction."""
    rows = format_exif({"Exif.Photo.ExposureTime": "2/1"})
    assert rows == [("Shutter", "2 s")]


def test_missing_and_empty_tags_skipped():
    """Tags that are absent or empty are omitted from the output."""
    rows = format_exif(
        {"Exif.Image.Model": "", "Exif.Photo.FNumber": "200/100"}
    )
    assert rows == [("Aperture", "f/2")]


def test_unparseable_rational_falls_back_to_raw():
    """A value that is not a valid rational is shown verbatim."""
    rows = format_exif({"Exif.Photo.FNumber": "weird"})
    assert rows == [("Aperture", "weird")]
