"""Batch export applies a RAF's geometry sidecar when one exists."""

from __future__ import annotations

from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Adw", "1")

gi.require_version("GExiv2", "0.10")

from gi.repository import GdkPixbuf, GExiv2

from grawji.crop import CropRotate
from grawji.imaging.export import (
    camera_file_format,
    sidecar_decode,
    stamp_for,
    with_border,
    write_camera_file,
)
from grawji.imaging.imagemeta import Stamp, photo_tags, with_credits
from grawji.imaging.render import add_border, scale_to_edge
from grawji.marks import Label, Marks
from grawji.photo_recipe import recipe_from_tags
from grawji.settings import Settings
from grawji.sidecar import save_crop, save_marks


def _jpeg_bytes(width: int, height: int) -> bytes:
    """Encode a plain filled image as JPEG bytes."""
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, False, 8, width, height
    )
    pixbuf.fill(0xFF8800FF)
    ok, buffer = pixbuf.save_to_bufferv("jpeg", ["quality"], ["90"])
    assert ok
    return bytes(buffer)


def test_sidecar_decode_without_sidecar(tmp_path: Path) -> None:
    """No sidecar (or identity) means the camera bytes pass through."""
    raf = tmp_path / "DSCF0001.RAF"
    raf.write_bytes(b"raf")
    assert sidecar_decode(str(raf)) is None
    save_crop(raf, CropRotate())  # identity writes no file
    assert sidecar_decode(str(raf)) is None


def test_sidecar_decode_applies_geometry(tmp_path: Path) -> None:
    """A stored crop/rotation is baked into the decoded pixels."""
    raf = tmp_path / "DSCF0002.RAF"
    raf.write_bytes(b"raf")
    save_crop(
        raf,
        CropRotate(orientation=90, rect=(0.25, 0.25, 0.5, 0.5)),
    )
    decode = sidecar_decode(str(raf))
    assert decode is not None
    pixbuf = decode(_jpeg_bytes(600, 400))
    assert (pixbuf.get_width(), pixbuf.get_height()) == (200, 300)


def test_add_border_dimensions_and_color() -> None:
    """The border adds percent-of-longer-edge on all four sides."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 600, 400)
    pixbuf.fill(0xFF8800FF)
    out = add_border(pixbuf, 5.0, "#000000")
    # 5% of 600 = 30 on each side.
    assert (out.get_width(), out.get_height()) == (660, 460)
    data, stride, n = (
        out.get_pixels(),
        out.get_rowstride(),
        out.get_n_channels(),
    )

    def px(x, y):
        offset = y * stride + x * n
        return tuple(data[offset : offset + 3])

    assert px(2, 2) == (0, 0, 0)
    assert px(660 - 3, 460 - 3) == (0, 0, 0)
    assert px(330, 230) == (255, 136, 0)
    assert add_border(pixbuf, 0.0, "#000000") is pixbuf
    white = add_border(pixbuf, 5.0, "not-a-color")
    wdata, wstride, wn = (
        white.get_pixels(),
        white.get_rowstride(),
        white.get_n_channels(),
    )
    assert tuple(wdata[2 * wstride + 2 * wn : 2 * wstride + 2 * wn + 3]) == (
        255,
        255,
        255,
    )


def test_with_border_wraps_only_when_enabled() -> None:
    """with_border is a passthrough unless framing would do anything."""
    decode = lambda jpeg: jpeg  # noqa: E731
    assert with_border(decode, Settings()) is decode
    disabled = Settings(export_border_enabled=False, export_border_percent=5)
    assert with_border(decode, disabled) is decode
    noop = Settings(export_border_enabled=True, export_border_percent=0.0)
    assert with_border(decode, noop) is decode
    on = Settings(
        export_border_enabled=True,
        export_border_percent=5.0,
        export_border_color="#000000",
    )
    wrapped = with_border(decode, on)
    assert wrapped is not decode
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 200, 100)
    pixbuf.fill(0xFF8800FF)
    out = with_border(lambda _j: pixbuf, on)(b"")
    assert (out.get_width(), out.get_height()) == (220, 120)


def test_add_border_pads_to_aspect() -> None:
    """The aspect fill extends the framed canvas, image centered."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 600, 400)
    pixbuf.fill(0xFF8800FF)
    out = add_border(pixbuf, 5.0, "#000000", 1.0)
    assert (out.get_width(), out.get_height()) == (660, 660)
    data, stride, n = (
        out.get_pixels(),
        out.get_rowstride(),
        out.get_n_channels(),
    )

    def px(x, y):
        offset = y * stride + x * n
        return tuple(data[offset : offset + 3])

    assert px(330, 5) == (0, 0, 0)
    assert px(330, 330) == (255, 136, 0)
    square = add_border(pixbuf, 0.0, "#000000", 1.0)
    assert (square.get_width(), square.get_height()) == (600, 600)
    assert add_border(pixbuf, 0.0, "#000000", 1.5) is pixbuf


def test_scale_to_edge_downscales():
    """The longer edge lands exactly on the limit, aspect kept."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 400, 300)
    scaled = scale_to_edge(pixbuf, 200)
    assert (scaled.get_width(), scaled.get_height()) == (200, 150)


def test_scale_to_edge_never_upscales():
    """Images already within the limit pass through untouched."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 100, 80)
    assert scale_to_edge(pixbuf, 200) is pixbuf
    assert scale_to_edge(pixbuf, 0) is pixbuf


def test_with_credits_stamps_exif(tmp_path):
    """Artist and copyright land in the EXIF, pixels stay identical."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 8)
    source = tmp_path / "plain.jpg"
    pixbuf.savev(str(source), "jpeg", [], [])
    jpeg = source.read_bytes()

    stamped = with_credits(
        jpeg, stamp=Stamp(artist="Jane Doe", rights="CC BY 4.0")
    )
    out = tmp_path / "stamped.jpg"
    out.write_bytes(stamped)
    meta = GExiv2.Metadata()
    meta.open_path(str(out))
    assert meta.try_get_tag_string("Exif.Image.Artist") == "Jane Doe"
    assert meta.try_get_tag_string("Exif.Image.Copyright") == "CC BY 4.0"
    # no stamp configured: bytes pass through untouched
    assert with_credits(jpeg, stamp=Stamp()) is jpeg


def test_with_credits_stamps_provenance(tmp_path):
    """The recipe provenance lands in the EXIF user comment."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 8)
    source = tmp_path / "plain.jpg"
    pixbuf.savev(str(source), "jpeg", [], [])
    jpeg = source.read_bytes()

    stamped = with_credits(jpeg, stamp=Stamp(comment="grawji recipe: Portra"))
    out = tmp_path / "stamped.jpg"
    out.write_bytes(stamped)
    meta = GExiv2.Metadata()
    meta.open_path(str(out))
    comment = meta.try_get_tag_string("Exif.Photo.UserComment") or ""
    assert "grawji recipe: Portra" in comment


def _plain_jpeg(tmp_path) -> bytes:
    """A tiny JPEG without metadata."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 8)
    source = tmp_path / "plain.jpg"
    pixbuf.savev(str(source), "jpeg", [], [])
    return source.read_bytes()


def _read(tmp_path, data: bytes) -> GExiv2.Metadata:
    """The metadata of data, read back from disk."""
    out = tmp_path / "stamped.jpg"
    out.write_bytes(data)
    meta = GExiv2.Metadata()
    meta.open_path(str(out))
    return meta


def test_the_rating_and_label_go_out_as_exif_and_xmp(tmp_path):
    """Other photo apps read either the EXIF or the XMP rating."""
    jpeg = _plain_jpeg(tmp_path)
    meta = _read(
        tmp_path, with_credits(jpeg, stamp=Stamp(rating=4, label="Red"))
    )
    assert meta.try_get_tag_string("Exif.Image.Rating") == "4"
    assert meta.try_get_tag_string("Exif.Image.RatingPercent") == "75"
    assert meta.try_get_tag_string("Xmp.xmp.Rating") == "4"
    assert meta.try_get_tag_string("Xmp.xmp.Label") == "Red"


def test_a_reject_is_xmp_only(tmp_path):
    """EXIF has no reject, so only the XMP rating carries the -1."""
    jpeg = _plain_jpeg(tmp_path)
    meta = _read(tmp_path, with_credits(jpeg, stamp=Stamp(rating=-1)))
    assert meta.try_get_tag_string("Xmp.xmp.Rating") == "-1"
    assert meta.try_get_tag_string("Exif.Image.Rating") is None


def test_the_stamp_reads_the_marks_from_the_sidecar(tmp_path):
    """The export takes its rating and first label from the sidecar."""
    raf = tmp_path / "a.RAF"
    raf.write_bytes(b"raf")
    save_marks(
        raf,
        Marks(rating=3, labels=frozenset({Label.BLUE, Label.GREEN})),
    )
    settings = Settings(export_artist="Jane Doe")
    stamp = stamp_for(settings, "grawji recipe: Portra", str(raf))
    assert stamp == Stamp(
        artist="Jane Doe",
        comment="grawji recipe: Portra",
        rating=3,
        label="Green",
    )
    settings.export_write_marks = False
    assert stamp_for(settings, "", str(raf)) == Stamp(artist="Jane Doe")


def _camera_jpeg(tmp_path, width=12, height=8) -> str:
    """A camera JPEG stand-in with a model tag."""
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, False, 8, width, height
    )
    pixbuf.fill(0x808080FF)
    path = tmp_path / "DSCF0001.JPG"
    pixbuf.savev(str(path), "jpeg", ["quality"], ["90"])
    meta = GExiv2.Metadata()
    meta.open_path(str(path))
    meta.try_set_tag_string("Exif.Image.Model", "X-E5")
    meta.save_file(str(path))
    return str(path)


def test_an_unedited_camera_jpeg_goes_out_byte_for_byte(tmp_path):
    """No edit and nothing to stamp keeps the camera's file as it is."""
    source = _camera_jpeg(tmp_path)
    out = str(tmp_path / "out.jpg")
    path, fmt = write_camera_file(
        source, out, settings=Settings(), crop=CropRotate()
    )
    assert (path, fmt) == (out, "jpeg")
    assert Path(path).read_bytes() == Path(source).read_bytes()


def test_an_edited_camera_jpeg_is_encoded_once_with_its_exif(tmp_path):
    """A crop decodes the JPEG, the camera EXIF comes back on."""
    source = _camera_jpeg(tmp_path)
    crop = CropRotate(rect=(0.0, 0.0, 0.5, 1.0))
    path, _fmt = write_camera_file(
        source, str(tmp_path / "out.jpg"), settings=Settings(), crop=crop
    )
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    assert (pixbuf.get_width(), pixbuf.get_height()) == (6, 8)
    meta = GExiv2.Metadata()
    meta.open_path(path)
    assert meta.try_get_tag_string("Exif.Image.Model") == "X-E5"


def test_a_camera_jpeg_never_becomes_a_tiff(tmp_path):
    """TIFF and HEIF would only wrap 8-bit JPEG pixels."""
    source = _camera_jpeg(tmp_path)
    settings = Settings(export_format="tiff16")
    path, fmt = write_camera_file(
        source, str(tmp_path / "out.tif"), settings=settings, crop=CropRotate()
    )
    assert fmt == "jpeg"
    assert path.endswith(".jpg")
    assert camera_file_format("heif") == "jpeg"


def test_photo_tags_read_a_fujifilm_makernote(tmp_path):
    """A JPEG carrying Fujifilm tags reads back through photo_tags."""
    path = tmp_path / "DSCF0001.JPG"
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 8, 8)
    pixbuf.savev(str(path), "jpeg", [], [])
    meta = GExiv2.Metadata()
    meta.open_path(str(path))
    meta.try_set_tag_string("Exif.Image.Make", "FUJIFILM")
    meta.try_set_tag_string("Exif.Image.Model", "X-E5")
    meta.try_set_tag_string("Exif.Fujifilm.FilmMode", "2816")
    meta.try_set_tag_string("Exif.Fujifilm.HighlightTone", "-8")
    meta.save_file(str(path))
    read = photo_tags(str(path))
    assert read is not None
    assert read("Exif.Fujifilm.FilmMode") == "2816"
    found = recipe_from_tags(read)
    assert found is not None
    assert found.recipe.film_simulation == "RealaAce"
    assert found.recipe.highlights == 0.5
    assert photo_tags(str(tmp_path / "missing.jpg")) is None
