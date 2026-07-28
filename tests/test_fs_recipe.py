"""Tests for the FS dial-preset encoder."""

import struct

import pytest

from grawji.backup_recipe import BackupWriteError
from grawji.fs_recipe import (
    _XE5,
    layout_for,
    unsupported_fields,
    write_fs_recipe,
    write_fs_recipes,
)
from grawji.recipe import Recipe

_CHECKSUM = 0x120


def _blank() -> bytearray:
    """A zeroed blob of the X-E5 size with a plausible checksum seed."""
    blob = bytearray(_XE5.blob_size)
    struct.pack_into("<H", blob, _CHECKSUM, 0x1000)
    return blob


def _field(blob: bytes, name: str, slot: int) -> int:
    field = _XE5.fields[name]
    off = field.offset + slot * field.step
    if field.step == 2 and name == "wb_kelvin":
        return struct.unpack_from("<H", blob, off)[0]
    return blob[off]


def test_verified_fs_dial_film_sim_codes():
    """All 20 X-E5 FS dial sim codes, hardware-verified by dial sweep."""
    blob = _blank()
    sim_off = _XE5.fields["film_sim"].offset + 2 * _XE5.fields["film_sim"].step
    verified = {
        "Provia": 0x01,
        "Astia": 0x02,
        "Velvia": 0x04,
        "Sepia": 0x06,
        "Monochrome": 0x09,
        "MonochromeR": 0x0A,
        "MonochromeYe": 0x0B,
        "MonochromeG": 0x0C,
        "ProNegStd": 0x0D,
        "ProNegHi": 0x0E,
        "ClassicChrome": 0x0F,
        "ClassicNeg": 0x10,
        "NostalgicNeg": 0x11,
        "RealaAce": 0x12,
        "Eterna": 0x13,
        "EternaBleach": 0x14,
        "Acros": 0x16,
        "AcrosR": 0x17,
        "AcrosYe": 0x18,
        "AcrosG": 0x19,
    }
    for name, code in verified.items():
        out = write_fs_recipe(blob, _XE5, 2, Recipe(film_simulation=name))
        assert out[sim_off] == code, name


def test_layout_for_known_and_unknown():
    """Only the X-E5 is mapped; other bodies yield None."""
    assert layout_for("X-E5") is _XE5
    assert layout_for("FUJIFILM X-E5") is _XE5
    assert layout_for("X-T3") is None
    assert layout_for(None) is None


def test_encodes_every_mapped_field_at_its_offset():
    """A full recipe lands each parameter at its FS3 offset, encoded."""
    recipe = Recipe(
        film_simulation="ClassicNeg",
        white_balance="Temperature",
        color_temp=5000,
        dynamic_range="DR200",
        grain="Weak",
        grain_size="Large",
        color_chrome="Strong",
        color_chrome_blue="Weak",
        smooth_skin="Strong",
        highlights=-1.5,
        shadows=1,
        color=3,
        sharpness=-2,
        noise_reduction=-3,
        clarity=4,
        wb_shift_r=2,
        wb_shift_b=-1,
    )
    out = write_fs_recipe(_blank(), _XE5, 2, recipe)  # FS3
    assert _field(out, "film_sim", 2) == 0x10  # Classic Neg
    assert _field(out, "wb_mode", 2) == 0x0A  # Temperature
    assert _field(out, "wb_kelvin", 2) == 5000
    assert _field(out, "dr", 2) == 2
    assert _field(out, "nr", 2) == 1  # -3 + 4
    assert _field(out, "clarity", 2) == 10  # 4 + 6
    assert _field(out, "color", 2) == 4  # 7 - 3
    assert _field(out, "sharpness", 2) == 6  # 4 - -2
    assert _field(out, "highlight", 2) == 1  # (-1.5 + 2) * 2
    assert _field(out, "shadow", 2) == 6  # (1 + 2) * 2
    assert _field(out, "color_chrome", 2) == 2
    assert _field(out, "color_chrome_blue", 2) == 1
    assert _field(out, "grain_rough", 2) == 1  # Weak
    assert _field(out, "grain_size", 2) == 1  # Large
    assert _field(out, "smooth_skin", 2) == 2
    assert _field(out, "wb_shift_r", 2) == 7  # 9 - 2
    assert _field(out, "wb_shift_b", 2) == 10  # 9 - -1


def test_slots_are_addressed_by_stride():
    """FS1/FS2/FS3 write to adjacent elements of each array."""
    blob = _blank()
    for slot in range(3):
        blob = write_fs_recipe(
            blob, _XE5, slot, Recipe(film_simulation="Provia", color=slot)
        )
    # The film-sim table has a 3-byte stride; the rest are adjacent.
    assert _field(blob, "film_sim", 0) == 0x01
    assert _field(blob, "film_sim", 1) == 0x01
    assert _field(blob, "film_sim", 2) == 0x01
    assert _XE5.fields["color"].offset == 34752
    assert _field(blob, "color", 0) == 7  # color 0
    assert _field(blob, "color", 1) == 6  # color +1
    assert _field(blob, "color", 2) == 5  # color +2


def test_checksum_delta_tracks_the_content_change():
    """The stored u16 checksum moves by exactly the byte delta."""
    blob = _blank()
    seed = struct.unpack_from("<H", blob, _CHECKSUM)[0]
    out = write_fs_recipe(blob, _XE5, 2, Recipe(film_simulation="Provia"))
    # Sum the changed content bytes (everything but the checksum field).
    delta = sum(
        out[i] - blob[i]
        for i in range(len(blob))
        if not _CHECKSUM <= i < _CHECKSUM + 2
    )
    got = struct.unpack_from("<H", out, _CHECKSUM)[0]
    assert got == (seed + delta) & 0xFFFF


def test_as_shot_leaves_wb_bytes_untouched():
    """An AsShot recipe never writes the WB mode/shift/kelvin arrays."""
    blob = _blank()
    off = _XE5.fields["wb_mode"].offset + 2
    blob[off] = 0x03  # a stored Daylight the recipe must not clobber
    out = write_fs_recipe(blob, _XE5, 2, Recipe())  # default AsShot
    assert out[off] == 0x03


def test_grain_off_skips_the_size_byte():
    """Grain Off writes roughness 2 and leaves the size element alone."""
    blob = _blank()
    size_off = _XE5.fields["grain_size"].offset + 2
    blob[size_off] = 1  # a stored Large the recipe must not touch
    out = write_fs_recipe(blob, _XE5, 2, Recipe(grain="Off"))
    assert _field(out, "grain_rough", 2) == 2
    assert out[size_off] == 1


def test_mono_toning_written_only_for_bw_sims():
    """Acros stores WC/MG as 18 - value; a colour sim leaves them alone."""
    blob = _blank()
    wc_off = _XE5.fields["mono_wc"].offset + 2
    mg_off = _XE5.fields["mono_mg"].offset + 2
    blob[wc_off] = 0x12  # neutral 18
    blob[mg_off] = 0x12
    # Hardware-verified encoding: WC -4 -> 22, MG +8 -> 10.
    out = write_fs_recipe(
        blob,
        _XE5,
        2,
        Recipe(
            film_simulation="Acros", mono_warm_cool=-4, mono_magenta_green=8
        ),
    )
    assert out[wc_off] == 22
    assert out[mg_off] == 10
    # A colour sim must not touch these dual-use bytes.
    out = write_fs_recipe(
        _blank(), _XE5, 2, Recipe(film_simulation="Provia", mono_warm_cool=5)
    )
    assert out[wc_off] == 0  # left at the blank blob's zero
    assert out[mg_off] == 0


def test_mono_toning_clamps_to_the_axis_limit():
    """Values beyond +-18 clamp so the byte stays in range."""
    blob = _blank()
    wc_off = _XE5.fields["mono_wc"].offset + 2
    out = write_fs_recipe(
        blob, _XE5, 2, Recipe(film_simulation="Monochrome", mono_warm_cool=99)
    )
    assert out[wc_off] == 0  # 18 - 18
    out = write_fs_recipe(
        blob,
        _XE5,
        2,
        Recipe(film_simulation="Monochrome", mono_warm_cool=-99),
    )
    assert out[wc_off] == 36  # 18 - (-18)


def test_unsupported_fields_flags_exposure():
    """Exposure compensation is reported as dropped; mono now writes."""
    fields = unsupported_fields(
        _XE5, Recipe(film_simulation="Acros", mono_warm_cool=3, exposure=1.0)
    )
    assert "monochromatic color toning" not in fields
    assert "exposure compensation" in fields


def test_unmapped_film_sim_is_dropped_not_written():
    """A body-unknown sim is reported and its byte left unchanged."""
    blob = _blank()
    sim_off = _XE5.fields["film_sim"].offset + 2 * _XE5.fields["film_sim"].step
    blob[sim_off] = 0x0F  # stored Classic Chrome
    # A hypothetical sim missing from the blob enum: force it via a name.
    recipe = Recipe(film_simulation="NotARealSim")
    fields = unsupported_fields(_XE5, recipe)
    assert any("film simulation" in f for f in fields)
    out = write_fs_recipe(blob, _XE5, 2, recipe)
    assert out[sim_off] == 0x0F


def test_slot_and_size_guards():
    """A bad slot or wrong blob size raises before any write."""
    with pytest.raises(BackupWriteError, match="out of range"):
        write_fs_recipe(_blank(), _XE5, 3, Recipe())
    with pytest.raises(BackupWriteError, match="expected"):
        write_fs_recipe(bytearray(100), _XE5, 0, Recipe())


def test_write_fs_recipes_batches_slots():
    """Batch writing several FS slots applies each one."""
    out = write_fs_recipes(
        _blank(),
        _XE5,
        {0: Recipe(film_simulation="Provia"), 2: Recipe(color=4)},
    )
    assert _field(out, "film_sim", 0) == 0x01
    assert _field(out, "color", 2) == 3  # 7 - 4


def test_unverified_film_sim_is_dropped_never_guessed():
    """A sim outside the verified dial table is dropped, not guessed.

    A wrong FS sim code is accepted silently and renders the wrong sim
    on the dial (the read-back cannot catch it), so anything not in the
    swept table must leave the sim byte alone and report the drop.
    """
    blob = _blank()
    sim_off = _XE5.fields["film_sim"].offset + 2 * _XE5.fields["film_sim"].step
    blob[sim_off] = 0x10  # a stored Classic Neg the write must not disturb
    recipe = Recipe(film_simulation="SomeFutureSim")
    assert any(
        "not yet verified" in f for f in unsupported_fields(_XE5, recipe)
    )
    out = write_fs_recipe(blob, _XE5, 2, recipe)
    assert out[sim_off] == 0x10  # unchanged
