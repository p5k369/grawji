"""Tests for embedding, finishing and developing a transplant."""

import io
import struct
from pathlib import Path

import numpy as np
import pytest
import rawji
from PIL import Image

from grawji.recipe import Recipe
from grawji.transplant import embed, output, pipeline

_PIXEL_BASE = 0x120


def _carrier_pixels():
    """A 12x12 carrier frame with unique values in every row."""
    return np.arange(144, dtype=np.uint16).reshape(12, 12)


def _pattern(rows):
    """A CFA letter pattern from a list of row strings."""
    return np.array([list(r) for r in rows])


# Two 6x6 X-Trans phases: the carrier is the donor rolled up one row.
_DONOR_PAT = _pattern(
    ["RBGBRG", "GGRGGB", "GGBGGR", "BRGRBG", "GGBGGR", "GGRGGB"]
)
_CARRIER_PAT = _pattern(
    ["GGRGGB", "GGBGGR", "BRGRBG", "GGBGGR", "GGRGGB", "RBGBRG"]
)


def _carrier_blob(pixels, wb: tuple[int, int, int] | None = None) -> bytes:
    """A fake carrier RAF: header, CFA container preamble, pixel array."""
    header = bytearray(0x100)
    struct.pack_into(">I", header, 0x64, 0x100)
    preamble = bytes(0x20)
    body = preamble + pixels.astype("<u2").tobytes()
    meta = b""
    if wb is not None:
        triple = struct.pack("<3H", *wb)
        meta = bytes(4) + triple + bytes(6) + triple
        struct.pack_into(">2I", header, 0x5C, 0x100 + len(body), len(meta))
    return bytes(header) + body + meta


def _png(image) -> bytes:
    """Encode a PIL image losslessly for deterministic pixel checks."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_locate_finds_array_behind_container_preamble():
    """The pixel array is found by row match past the CFA offset."""
    pixels = _carrier_pixels()
    base = embed.locate_pixel_base(_carrier_blob(pixels), pixels)
    assert base == _PIXEL_BASE


def test_locate_missing_array_raises():
    """A blob without the array reads as a compressed carrier."""
    pixels = _carrier_pixels()
    with pytest.raises(embed.TransplantError, match="uncompressed"):
        embed.locate_pixel_base(bytes(0x200), pixels)


def test_embed_places_donor_top_left_with_black_shift():
    """The donor lands top left, black-shifted, padding untouched."""
    carrier = _carrier_pixels()
    donor = np.full((6, 6), 500, dtype=np.uint16)
    frame = embed.embed_frame(carrier, donor, 3)
    assert (frame[:6, :6] == 503).all()
    assert (frame[6:, :] == carrier[6:, :]).all()
    assert (frame[:6, 6:] == carrier[:6, 6:]).all()


def test_embed_clips_to_14_bit_ceiling():
    """The black-level shift cannot push values past 16383."""
    carrier = _carrier_pixels()
    donor = np.full((6, 6), 16380, dtype=np.uint16)
    frame = embed.embed_frame(carrier, donor, 10)
    assert (frame[:6, :6] == 16383).all()


def test_embed_oversized_donor_raises():
    """A donor larger than the carrier frame is refused."""
    carrier = _carrier_pixels()
    donor = np.zeros((13, 12), dtype=np.uint16)
    with pytest.raises(embed.TransplantError, match="does not fit"):
        embed.embed_frame(carrier, donor, 0)


def test_embed_places_donor_at_offset():
    """A phase offset shifts the donor down and right in the frame."""
    carrier = _carrier_pixels()
    donor = np.full((6, 6), 500, dtype=np.uint16)
    frame = embed.embed_frame(carrier, donor, 0, offset=(2, 3))
    assert (frame[2:8, 3:9] == 500).all()
    assert frame[0, 0] == carrier[0, 0]  # top-left is untouched padding


def test_embed_offset_overflow_raises():
    """A donor that overruns the frame at its offset is refused."""
    carrier = _carrier_pixels()
    donor = np.zeros((12, 12), dtype=np.uint16)
    with pytest.raises(embed.TransplantError, match="does not fit"):
        embed.embed_frame(carrier, donor, 0, offset=(1, 0))


def test_cfa_phase_offset_finds_the_roll():
    """The offset aligns the donor pattern onto the carrier's phase."""
    dy, dx = embed.cfa_phase_offset(_DONOR_PAT, _CARRIER_PAT)
    assert (dy, dx) == (2, 3)
    rolled = np.roll(np.roll(_CARRIER_PAT, -dy, axis=0), -dx, axis=1)
    assert np.array_equal(rolled, _DONOR_PAT)


def test_cfa_phase_offset_identity():
    """Identical patterns need no shift."""
    assert embed.cfa_phase_offset(_DONOR_PAT, _DONOR_PAT) == (0, 0)


def test_cfa_phase_offset_incompatible_raises():
    """Patterns that share no phase are rejected."""
    other = _pattern(["RGGGGG"] * 6)
    with pytest.raises(embed.TransplantError, match="phase-align"):
        embed.cfa_phase_offset(_DONOR_PAT, other)


def test_build_transplant_splices_donor_into_carrier(tmp_path, monkeypatch):
    """The patched RAF carries the shifted donor at the pixel base."""
    carrier_pixels = _carrier_pixels()
    donor_pixels = np.full((6, 4), 500, dtype=np.uint16)
    carrier = tmp_path / "carrier.RAF"
    carrier.write_bytes(_carrier_blob(carrier_pixels))
    donor = tmp_path / "donor.RAF"
    donor.write_bytes(b"donor")
    same = _pattern(["RG", "GB"])
    frames = {
        carrier: embed._RawFrame(carrier_pixels, 1022, 0, same),
        donor: embed._RawFrame(donor_pixels, 1019, 6, same),
    }
    monkeypatch.setattr(embed, "decode", lambda path: frames[path])

    result = embed.build_transplant(donor, carrier, context=0)

    assert result.flip == 6
    assert result.donor_size == (6, 4)
    assert result.carrier_size == (12, 12)
    assert result.offset == (0, 0)
    patched = np.frombuffer(
        result.raf[_PIXEL_BASE : _PIXEL_BASE + 288], dtype="<u2"
    ).reshape(12, 12)
    assert (patched[:6, :4] == 503).all()
    assert (patched[6:, :] == carrier_pixels[6:, :]).all()


def test_build_transplant_applies_cfa_phase_offset(tmp_path, monkeypatch):
    """A phase-shifted donor is placed at the computed CFA offset."""
    carrier_pixels = _carrier_pixels()
    donor_pixels = np.full((6, 6), 500, dtype=np.uint16)
    carrier = tmp_path / "carrier.RAF"
    carrier.write_bytes(_carrier_blob(carrier_pixels))
    donor = tmp_path / "donor.RAF"
    donor.write_bytes(b"donor")
    frames = {
        carrier: embed._RawFrame(carrier_pixels, 1022, 0, _CARRIER_PAT),
        donor: embed._RawFrame(donor_pixels, 1022, 0, _DONOR_PAT),
    }
    monkeypatch.setattr(embed, "decode", lambda path: frames[path])

    result = embed.build_transplant(donor, carrier)

    assert result.offset == (2, 3)
    patched = np.frombuffer(
        result.raf[_PIXEL_BASE : _PIXEL_BASE + 288], dtype="<u2"
    ).reshape(12, 12)
    assert (patched[2:8, 3:9] == 500).all()
    assert (patched[0, 0] == carrier_pixels[0, 0]).all()


def test_scale_exposure_works_on_black_subtracted_values():
    """Scaling halves the signal above black, not the raw value."""
    pixels = np.array([[1019, 2019, 5019]], dtype=np.uint16)
    scaled = embed.scale_exposure(pixels, 1019, 0.25)
    assert scaled.tolist() == [[1019, 1269, 2019]]


def test_scale_exposure_identity_and_range():
    """Scale 1.0 is a no-op; out-of-range factors are refused."""
    pixels = np.array([[123]], dtype=np.uint16)
    assert embed.scale_exposure(pixels, 100, 1.0) is pixels
    with pytest.raises(embed.TransplantError, match="14-bit"):
        embed.scale_exposure(pixels, 100, 2.0)
    with pytest.raises(embed.TransplantError, match="14-bit"):
        embed.scale_exposure(pixels, 100, 0.0)


def test_build_transplant_scales_the_donor_exposure(tmp_path, monkeypatch):
    """exposure_scale reaches the embedded pixels (DR compensation)."""
    carrier_pixels = _carrier_pixels()
    donor_pixels = np.full((6, 4), 5019, dtype=np.uint16)
    carrier = tmp_path / "carrier.RAF"
    carrier.write_bytes(_carrier_blob(carrier_pixels))
    donor = tmp_path / "donor.RAF"
    donor.write_bytes(b"donor")
    same = _pattern(["RG", "GB"])
    frames = {
        carrier: embed._RawFrame(carrier_pixels, 1022, 0, same),
        donor: embed._RawFrame(donor_pixels, 1019, 0, same),
    }
    monkeypatch.setattr(embed, "decode", lambda path: frames[path])

    result = embed.build_transplant(donor, carrier, exposure_scale=0.25)

    patched = np.frombuffer(
        result.raf[_PIXEL_BASE : _PIXEL_BASE + 288], dtype="<u2"
    ).reshape(12, 12)
    # (5019-1019)*0.25 + 1019 = 2019, then black delta +3.
    assert (patched[:6, :4] == 2022).all()


def test_transfer_wb_replaces_all_meta_copies():
    """Every copy of the carrier triple inside the meta block moves."""
    carrier_wb, donor_wb = (302, 536, 561), (302, 320, 1012)
    triple = struct.pack("<3H", *carrier_wb)
    header = bytearray(0x100)
    meta = bytes(2) + triple + bytes(4) + triple
    struct.pack_into(">2I", header, 0x5C, 0x100, len(meta))
    # a value collision AFTER the meta block must stay untouched
    blob = bytearray(bytes(header) + meta + triple)

    count = embed.transfer_as_shot_wb(blob, carrier_wb, donor_wb)

    assert count == 2
    donor_bytes = struct.pack("<3H", *donor_wb)
    assert blob[0x102 : 0x102 + 6] == donor_bytes
    assert blob[0x10C : 0x10C + 6] == donor_bytes
    assert blob[-6:] == triple


def test_transfer_wb_equal_triples_is_a_noop():
    """A donor shot at the carrier's exact WB changes nothing."""
    blob = bytearray(0x120)
    assert embed.transfer_as_shot_wb(blob, (1, 2, 3), (1, 2, 3)) == 0
    assert blob == bytearray(0x120)


def test_transfer_wb_missing_triple_raises():
    """An unlocatable carrier triple is an error, not a silent skip."""
    header = bytearray(0x100)
    struct.pack_into(">2I", header, 0x5C, 0x100, 8)
    blob = bytearray(bytes(header) + bytes(8))
    with pytest.raises(embed.TransplantError, match="white-balance"):
        embed.transfer_as_shot_wb(blob, (302, 536, 561), (302, 320, 1012))


def test_build_transplant_transfers_the_donor_wb(tmp_path, monkeypatch):
    """The donor's baked WB triple replaces the carrier's copies."""
    carrier_pixels = _carrier_pixels()
    carrier_wb, donor_wb = (302, 536, 561), (302, 320, 1012)
    carrier = tmp_path / "carrier.RAF"
    carrier.write_bytes(_carrier_blob(carrier_pixels, wb=carrier_wb))
    donor = tmp_path / "donor.RAF"
    donor.write_bytes(b"donor")
    same = _pattern(["RG", "GB"])
    frames = {
        carrier: embed._RawFrame(carrier_pixels, 1022, 0, same, wb=carrier_wb),
        donor: embed._RawFrame(
            np.full((6, 4), 500, dtype=np.uint16), 1019, 0, same, wb=donor_wb
        ),
    }
    monkeypatch.setattr(embed, "decode", lambda path: frames[path])

    result = embed.build_transplant(donor, carrier)

    assert result.raf.count(struct.pack("<3H", *donor_wb)) == 2
    assert struct.pack("<3H", *carrier_wb) not in result.raf


def test_build_transplant_prefers_the_plain_auto_wb_levels(
    tmp_path, monkeypatch
):
    """An Auto WB donor hands the engine its plain auto levels."""
    carrier_pixels = _carrier_pixels()
    carrier_wb, tuned_wb, auto_wb = (
        (302, 536, 561),
        (302, 631, 495),
        (302, 594, 561),
    )
    carrier = tmp_path / "carrier.RAF"
    carrier.write_bytes(_carrier_blob(carrier_pixels, wb=carrier_wb))
    donor = tmp_path / "donor.RAF"
    donor.write_bytes(b"donor")
    same = _pattern(["RG", "GB"])
    frames = {
        carrier: embed._RawFrame(carrier_pixels, 1022, 0, same, wb=carrier_wb),
        donor: embed._RawFrame(
            np.full((6, 4), 500, dtype=np.uint16), 1019, 0, same, wb=tuned_wb
        ),
    }
    monkeypatch.setattr(embed, "decode", lambda path: frames[path])
    monkeypatch.setattr(
        embed.imagemeta,
        "fuji_auto_wb_levels",
        lambda path: auto_wb if path == str(donor) else None,
    )

    result = embed.build_transplant(donor, carrier)

    assert result.raf.count(struct.pack("<3H", *auto_wb)) == 2
    assert struct.pack("<3H", *tuned_wb) not in result.raf


def test_build_transplant_undoes_the_donor_vignetting(tmp_path, monkeypatch):
    """donor_lens scales the donor's visible area before it is embedded."""
    carrier_pixels = _carrier_pixels()
    carrier = tmp_path / "carrier.RAF"
    carrier.write_bytes(_carrier_blob(carrier_pixels))
    donor = tmp_path / "donor.RAF"
    donor.write_bytes(b"donor")
    same = _pattern(["RG", "GB"])
    donor_pixels = np.full((6, 4), 1019 + 400, dtype=np.uint16)
    frames = {
        carrier: embed._RawFrame(carrier_pixels, 1022, 0, same),
        donor: embed._RawFrame(
            donor_pixels, 1019, 0, same, visible=(0, 0, 6, 4)
        ),
    }
    monkeypatch.setattr(embed, "decode", lambda path: frames[path])
    seen = {}

    def fake_undo(pixels, black, visible, tables):
        seen.update(black=black, visible=visible, tables=tables)
        return pixels + 100

    monkeypatch.setattr(embed, "undo_vignetting", fake_undo)
    tables = object()

    result = embed.build_transplant(
        donor, carrier, donor_lens=tables, context=0
    )

    assert seen == {"black": 1019, "visible": (0, 0, 6, 4), "tables": tables}
    # the lifted donor values (plus the black-level delta of 3) land in the frame
    base = embed.locate_pixel_base(result.raf, carrier_pixels)
    first = struct.unpack_from("<H", result.raf, base)[0]
    assert first == 1019 + 400 + 100 + 3


def test_finish_crops_to_donor_fraction_and_rotates():
    """The render is cropped by the donor/carrier ratio and rotated."""
    image = Image.new("RGB", (120, 120))
    image.paste((200, 30, 30), (0, 0, 40, 60))
    result = output.finish_jpeg(
        _png(image), flip=6, donor_size=(6, 4), carrier_size=(12, 12)
    )
    # 40x60 donor crop, 2 px seam inset per edge, rotated -90.
    assert Image.open(io.BytesIO(result)).size == (56, 36)


def test_finish_crops_from_the_offset_origin():
    """The crop starts at the donor's offset, not the frame corner."""
    image = Image.new("RGB", (120, 120))
    # donor 6x6 in a 12x12 carrier at offset (2, 3) -> render offset
    # (20, 30), size (60, 60); minus the 2 px seam inset per edge.
    image.paste((200, 30, 30), (30, 20, 90, 80))
    result = output.finish_jpeg(
        _png(image),
        flip=0,
        donor_size=(6, 6),
        carrier_size=(12, 12),
        offset=(2, 3),
    )
    assert Image.open(io.BytesIO(result)).size == (56, 56)


def test_finish_insets_the_boundary_seam():
    """Bright seam pixels at the crop edges do not survive the inset."""
    image = Image.new("RGB", (120, 120), (30, 30, 60))
    image.paste((255, 255, 255), (0, 58, 60, 60))
    result = output.finish_jpeg(
        _png(image), flip=0, donor_size=(6, 6), carrier_size=(12, 12)
    )
    out = Image.open(io.BytesIO(result))
    assert out.size == (56, 56)
    assert max(out.getextrema()[0]) < 200


def test_finish_keeps_night_content():
    """Content darker than any threshold is not eaten by the inset."""
    image = Image.new("RGB", (120, 120), (5, 5, 9))
    result = output.finish_jpeg(
        _png(image), flip=0, donor_size=(6, 6), carrier_size=(12, 12)
    )
    assert Image.open(io.BytesIO(result)).size == (56, 56)


class FakeCamera:
    """Records the rawji camera calls and serves a canned render."""

    def __init__(self, render: bytes):
        """Serve render as the conversion result."""
        self.render = render
        self.calls = []
        self.sent_raf = None
        self.profile_written = None

    def send_raf(self, filepath):
        """Record the RAF bytes as they exist at call time."""
        self.calls.append("send_raf")
        self.sent_raf = Path(filepath).read_bytes()

    def get_profile(self):
        """Return an all-zero 629-byte profile."""
        self.calls.append("get_profile")
        return bytes(629)

    def set_profile(self, profile):
        """Record the patched profile."""
        self.calls.append("set_profile")
        self.profile_written = profile

    def trigger_conversion(self, full_resolution=True):
        """Record the trigger and its resolution flag."""
        self.calls.append(f"trigger:{full_resolution}")

    def wait_for_result(self, timeout=30, thumbnail=False):
        """Record the wait and return the canned render."""
        self.calls.append(f"wait:{timeout}")
        return self.render


def _transplant(raf: bytes = b"patched-carrier") -> "embed.Transplant":
    """A minimal Transplant with a 6x6 donor in a 12x12 carrier."""
    return embed.Transplant(
        raf=raf,
        flip=0,
        donor_size=(6, 6),
        carrier_size=(12, 12),
        offset=(0, 0),
    )


def test_develop_runs_full_flow_in_order(tmp_path):
    """The pipeline sends, patches, converts and finishes in order."""
    render = _png(Image.new("RGB", (60, 60), (220, 220, 220)))
    camera = FakeCamera(render)
    options = pipeline.RenderOptions(film_sim="Eterna", wb_shift=(2, -2))

    jpeg = pipeline.develop(camera, _transplant(), tmp_path, options)

    assert camera.calls == [
        "send_raf",
        "get_profile",
        "set_profile",
        "trigger:True",
        "wait:300",
    ]
    assert camera.sent_raf == b"patched-carrier"
    code = struct.unpack_from("<I", camera.profile_written, 541)[0]
    assert code == int(rawji.FilmSimulation["Eterna"])
    assert struct.unpack_from("<i", camera.profile_written, 561)[0] == 2
    assert not (tmp_path / "transplant.RAF").exists()
    assert Image.open(io.BytesIO(jpeg)).size == (26, 26)


def test_develop_applies_recipe_and_ev(tmp_path):
    """A full Recipe goes through core.apply_recipe plus the EV sum."""
    render = _png(Image.new("RGB", (60, 60), (220, 220, 220)))
    camera = FakeCamera(render)
    recipe = Recipe(
        film_simulation="Eterna", dynamic_range="Auto", exposure=0.33
    )
    options = pipeline.RenderOptions(recipe=recipe, ev=-2.0)

    pipeline.develop(camera, _transplant(), tmp_path, options)

    profile = camera.profile_written
    code = struct.unpack_from("<I", profile, 541)[0]
    assert code == int(rawji.FilmSimulation["Eterna"])
    assert struct.unpack_from("<I", profile, 533)[0] == 0
    assert struct.unpack_from("<i", profile, 529)[0] == -1670


def test_develop_rejects_out_of_range_ev_sum(tmp_path):
    """An EV sum below -2 raises instead of rendering at 0 EV."""
    camera = FakeCamera(b"")
    recipe = Recipe(dynamic_range="Auto", exposure=-1.0)
    options = pipeline.RenderOptions(recipe=recipe, ev=-2.0)

    with pytest.raises(ValueError, match="honored"):
        pipeline.develop(camera, _transplant(), tmp_path, options)
    assert not (tmp_path / "transplant.RAF").exists()


def test_develop_removes_temp_raf_on_camera_error(tmp_path):
    """The temp RAF does not survive a camera failure."""

    class FailingCamera(FakeCamera):
        def get_profile(self):
            raise RuntimeError("0x2002")

    camera = FailingCamera(b"")
    with pytest.raises(RuntimeError):
        pipeline.develop(camera, _transplant(b"x"), tmp_path)
    assert not (tmp_path / "transplant.RAF").exists()


def test_finish_cuts_the_frame_out_of_a_letterboxed_thumbnail():
    """A 4:3 thumbnail of a 3:2 carrier loses its black bars first."""
    canvas = Image.new("RGB", (640, 480), (0, 0, 0))
    frame_h = round(640 * 2 / 3)
    top = (480 - frame_h) // 2
    canvas.paste((200, 120, 40), (0, top, 640, top + frame_h))
    out = output.finish_jpeg(
        _png(canvas),
        flip=0,
        donor_size=(12, 18),
        carrier_size=(12, 18),
        letterboxed=True,
    )
    image = Image.open(io.BytesIO(out))
    assert abs(image.width / image.height - 1.5) < 0.02
    assert (
        max(
            abs(a - b)
            for a, b in zip(
                image.getpixel((5, 5)), (200, 120, 40), strict=True
            )
        )
        < 8
    )


def test_finish_leaves_a_full_frame_thumbnail_alone():
    """A body that falls back to the full preview has no bars to cut."""
    frame = Image.new("RGB", (600, 400), (10, 200, 10))
    out = output.finish_jpeg(
        _png(frame),
        flip=0,
        donor_size=(12, 18),
        carrier_size=(12, 18),
        letterboxed=True,
    )
    image = Image.open(io.BytesIO(out))
    assert image.size[0] / image.size[1] == pytest.approx(1.5, abs=0.02)


def test_build_transplant_embeds_only_the_donor_picture(tmp_path, monkeypatch):
    """The donor's masked rows and columns stay out of the carrier."""
    carrier_pixels = _carrier_pixels()
    carrier = tmp_path / "carrier.RAF"
    carrier.write_bytes(_carrier_blob(carrier_pixels))
    donor = tmp_path / "donor.RAF"
    donor.write_bytes(b"donor")
    same = _pattern(["RG", "GB"])
    donor_pixels = np.full((6, 4), 1019 + 300, dtype=np.uint16)
    donor_pixels[:, 3] = 1019  # a masked column at the right
    frames = {
        carrier: embed._RawFrame(carrier_pixels, 1022, 0, same),
        donor: embed._RawFrame(
            donor_pixels,
            1019,
            0,
            same,
            visible=(0, 0, 6, 3),
            frame=(0, 0, 6, 3),
        ),
    }
    monkeypatch.setattr(embed, "decode", lambda path: frames[path])

    result = embed.build_transplant(donor, carrier, context=0)

    assert result.donor_size == (6, 3)
    base = embed.locate_pixel_base(result.raf, carrier_pixels)
    width = carrier_pixels.shape[1]
    row0 = struct.unpack_from(f"<{width}H", result.raf, base)
    assert row0[:3] == (1322, 1322, 1322)
    assert (
        row0[3] == carrier_pixels[0, 3]
    )  # the carrier's own pixel, not 1019+3


def test_place_donor_lands_inside_the_carrier_picture_on_phase():
    """The donor origin goes to the first on-phase spot in the picture."""
    carrier = embed._RawFrame(
        np.zeros((4182, 6384), dtype=np.uint16),
        1022,
        0,
        _CARRIER_PAT,
        frame=(13, 6, 4160, 6240),
    )
    donor = embed._RawFrame(
        np.zeros((4032, 6160), dtype=np.uint16),
        1019,
        0,
        _DONOR_PAT,
        frame=(16, 16, 4000, 6000),
    )
    top, left = embed.place_donor(donor, carrier)
    assert (top, left) == (18, 7)
    assert top >= 13
    assert left >= 6
    phase = embed.cfa_phase_offset(_DONOR_PAT, _CARRIER_PAT)
    assert ((top - 16 - phase[0]) % 6, (left - 16 - phase[1]) % 6) == (0, 0)


def test_place_donor_without_frames_is_the_phase_offset():
    """Frameless arrays place the donor at the bare phase shift."""
    carrier = embed._RawFrame(_carrier_pixels(), 1022, 0, _CARRIER_PAT)
    donor = embed._RawFrame(
        np.zeros((6, 6), dtype=np.uint16), 1019, 0, _DONOR_PAT
    )
    assert embed.place_donor(donor, carrier) == embed.cfa_phase_offset(
        _DONOR_PAT, _CARRIER_PAT
    )


def test_place_donor_refuses_a_picture_too_large_for_the_carriers():
    """An X-E5 picture does not fit the X-T3's, whatever the array."""
    carrier = embed._RawFrame(
        np.zeros((4182, 6384), dtype=np.uint16),
        1022,
        0,
        _CARRIER_PAT,
        frame=(13, 6, 4160, 6240),
    )
    donor = embed._RawFrame(
        np.zeros((5196, 7872), dtype=np.uint16),
        1019,
        0,
        _CARRIER_PAT,
        frame=(21, 12, 5152, 7728),
    )
    with pytest.raises(embed.TransplantError, match="does not fit"):
        embed.place_donor(donor, carrier)


def test_build_transplant_reports_picture_coordinates(tmp_path, monkeypatch):
    """Sizes and offset describe the pictures, not the raw arrays."""
    carrier_pixels = _carrier_pixels()
    carrier = tmp_path / "carrier.RAF"
    carrier.write_bytes(_carrier_blob(carrier_pixels))
    donor = tmp_path / "donor.RAF"
    donor.write_bytes(b"donor")
    same = _pattern(["RG", "GB"])
    donor_pixels = np.full((6, 6), 1019 + 300, dtype=np.uint16)
    frames = {
        carrier: embed._RawFrame(
            carrier_pixels, 1022, 0, same, frame=(3, 1, 8, 8)
        ),
        donor: embed._RawFrame(
            donor_pixels, 1019, 0, same, frame=(1, 1, 4, 5)
        ),
    }
    monkeypatch.setattr(embed, "decode", lambda path: frames[path])

    result = embed.build_transplant(donor, carrier)

    assert result.carrier_size == (8, 8)
    assert result.donor_size == (4, 5)
    assert result.offset == (0, 0)
    patched = np.frombuffer(
        result.raf[_PIXEL_BASE : _PIXEL_BASE + 288], dtype="<u2"
    ).reshape(12, 12)
    assert (patched[3:7, 1:6] == 1322).all()
    assert patched[2, 1] == carrier_pixels[2, 1]
    assert patched[3, 6] == carrier_pixels[3, 6]


def test_context_index_uses_the_border_then_repeats_the_edge_period():
    """Real border indices inside the range, same-phase repeats beyond."""
    idx = embed._context_index(4, 10, 2, 16, 4, 2)
    assert list(idx[:4]) == [2, 3, 2, 3]
    assert list(idx[4:14]) == list(range(4, 14))
    assert list(idx[14:]) == [14, 15, 14, 15]
    assert all((i - j) % 2 == 0 for i, j in zip(idx, range(18), strict=True))


def test_context_margin_steps_down_to_what_the_carrier_can_hold():
    """Two periods when there is room, less when not, zero at the end."""
    carrier = embed._RawFrame(
        np.zeros((60, 60), dtype=np.uint16), 1022, 0, _CARRIER_PAT
    )
    small = embed._RawFrame(
        np.zeros((12, 12), dtype=np.uint16), 1019, 0, _DONOR_PAT
    )
    assert embed.context_margin(small, carrier) == 12
    tight = embed._RawFrame(
        np.zeros((42, 42), dtype=np.uint16), 1019, 0, _DONOR_PAT
    )
    assert embed.context_margin(tight, carrier) == 6
    full = embed._RawFrame(
        np.zeros((54, 54), dtype=np.uint16), 1019, 0, _DONOR_PAT
    )
    assert embed.context_margin(full, carrier) == 0
    with pytest.raises(embed.TransplantError, match="does not fit"):
        embed.context_margin(carrier, full)


def test_build_transplant_embeds_picture_context(tmp_path, monkeypatch):
    """The sensor border rides along, missing rows repeat the edge period."""
    carrier_pixels = np.arange(48 * 48, dtype=np.uint16).reshape(48, 48)
    carrier = tmp_path / "carrier.RAF"
    carrier.write_bytes(_carrier_blob(carrier_pixels))
    donor = tmp_path / "donor.RAF"
    donor.write_bytes(b"donor")
    same = _pattern(["RG", "GB"])
    donor_pixels = (1019 + np.arange(30 * 30) % 500).astype(np.uint16)
    donor_pixels = donor_pixels.reshape(30, 30)
    frames = {
        carrier: embed._RawFrame(
            carrier_pixels, 1022, 0, same, frame=(6, 6, 36, 36)
        ),
        donor: embed._RawFrame(
            donor_pixels, 1019, 0, same, frame=(6, 6, 18, 18)
        ),
    }
    monkeypatch.setattr(embed, "decode", lambda path: frames[path])

    result = embed.build_transplant(donor, carrier)
    assert result.context == 8
    assert result.donor_size == (18, 18)
    assert result.carrier_size == (36, 36)
    assert result.offset == (8, 8)
    patched = np.frombuffer(
        result.raf[_PIXEL_BASE : _PIXEL_BASE + 48 * 48 * 2], dtype="<u2"
    ).reshape(48, 48)
    lifted = donor_pixels.astype(int) + 3
    assert (patched[14:32, 14:32] == lifted[6:24, 6:24]).all()
    assert (patched[8:14, 8:14] == lifted[:6, :6]).all()
    assert (patched[6, 8:14] == lifted[0, :6]).all()
    assert (patched[7, 8:14] == lifted[1, :6]).all()
    assert (patched[38, 14:32] == lifted[28, 6:24]).all()
    assert (patched[39, 14:32] == lifted[29, 6:24]).all()
    assert patched[5, 5] == carrier_pixels[5, 5]
    assert patched[40, 40] == carrier_pixels[40, 40]


def test_build_transplant_copies_a_shared_sensor_whole(tmp_path, monkeypatch):
    """Same array, same picture, same phase: the whole donor array goes in."""
    carrier_pixels = np.arange(24 * 24, dtype=np.uint16).reshape(24, 24)
    carrier = tmp_path / "carrier.RAF"
    carrier.write_bytes(_carrier_blob(carrier_pixels))
    donor = tmp_path / "donor.RAF"
    donor.write_bytes(b"donor")
    donor_pixels = (2000 + np.arange(24 * 24) % 300).astype(np.uint16)
    donor_pixels = donor_pixels.reshape(24, 24)
    frames = {
        carrier: embed._RawFrame(
            carrier_pixels, 1022, 0, _DONOR_PAT, frame=(6, 6, 12, 12)
        ),
        donor: embed._RawFrame(
            donor_pixels, 1019, 0, _DONOR_PAT, frame=(6, 6, 12, 12)
        ),
    }
    monkeypatch.setattr(embed, "decode", lambda path: frames[path])

    result = embed.build_transplant(donor, carrier)

    assert result.whole
    assert result.seamless
    assert result.context == 0
    assert result.donor_size == result.carrier_size == (12, 12)
    assert result.offset == (0, 0)
    patched = np.frombuffer(
        result.raf[_PIXEL_BASE : _PIXEL_BASE + 24 * 24 * 2], dtype="<u2"
    ).reshape(24, 24)
    assert (patched == donor_pixels.astype(int) + 3).all()


def test_same_geometry_needs_shape_picture_and_phase():
    """A shifted picture or a rolled phase falls back to placement."""
    pixels = np.zeros((12, 12), dtype=np.uint16)
    base = embed._RawFrame(pixels, 1000, 0, _DONOR_PAT, frame=(0, 0, 12, 12))
    assert embed.same_geometry(base, base)
    assert not embed.same_geometry(
        base, embed._RawFrame(pixels, 1000, 0, _DONOR_PAT, frame=(0, 0, 6, 6))
    )
    assert not embed.same_geometry(
        base, embed._RawFrame(pixels, 1000, 0, _CARRIER_PAT, frame=base.frame)
    )
    assert not embed.same_geometry(
        base,
        embed._RawFrame(
            np.zeros((18, 12), dtype=np.uint16), 1000, 0, _DONOR_PAT
        ),
    )
    assert embed.Transplant(b"", 0, (1, 1), (1, 1), (0, 0), context=6).seamless
    assert not embed.Transplant(b"", 0, (1, 1), (1, 1), (0, 0)).seamless


def test_finish_keeps_the_full_picture_without_the_inset():
    """With picture context embedded the crop is the exact donor size."""
    image = Image.new("RGB", (120, 120), (30, 30, 60))
    result = output.finish_jpeg(
        _png(image),
        flip=0,
        donor_size=(6, 6),
        carrier_size=(12, 12),
        inset=False,
    )
    assert Image.open(io.BytesIO(result)).size == (60, 60)
