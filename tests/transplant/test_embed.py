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
    monkeypatch.setattr(embed, "_decode", lambda path: frames[path])

    result = embed.build_transplant(donor, carrier)

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
    monkeypatch.setattr(embed, "_decode", lambda path: frames[path])

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
    monkeypatch.setattr(embed, "_decode", lambda path: frames[path])

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
    monkeypatch.setattr(embed, "_decode", lambda path: frames[path])

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
    monkeypatch.setattr(embed, "_decode", lambda path: frames[path])
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
    monkeypatch.setattr(embed, "_decode", lambda path: frames[path])
    seen = {}

    def fake_undo(pixels, black, visible, tables):
        seen.update(black=black, visible=visible, tables=tables)
        return pixels + 100

    monkeypatch.setattr(embed, "undo_vignetting", fake_undo)
    tables = object()

    result = embed.build_transplant(donor, carrier, donor_lens=tables)

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
