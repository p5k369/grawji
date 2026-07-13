"""Tests for the per-body capability table."""

import struct

import pytest
from rawji.fuji_profile import create_profile_simple

from grawji.capabilities import (
    BASELINE,
    FILM_SIMULATIONS,
    capabilities_for,
    is_xprocessor5,
    read_iopcode,
)
from grawji.core import FILM_SIM_CODES


def test_film_simulations_match_rawjis_enum():
    """The hand-ordered film-sim tuple covers rawji's enum completely."""
    assert set(FILM_SIMULATIONS) == set(FILM_SIM_CODES)


def _profile_with_iopcode(iopcode: str) -> bytes:
    """Build a full-length profile carrying an IOPCode string."""
    return bytes(create_profile_simple(iopcode=iopcode))


def test_read_iopcode_round_trips():
    """The IOPCode string in the header is read back as an int."""
    assert read_iopcode(_profile_with_iopcode("FF159502")) == 0xFF159502


def test_read_iopcode_handles_short_profile():
    """A too-short profile yields None rather than raising."""
    assert read_iopcode(b"\x1d\x00") is None


def test_read_iopcode_handles_non_hex():
    """A non-hex IOPCode string yields None."""
    bad = bytearray(40)
    struct.pack_into("<H", bad, 0, 0x1D)
    bad[2] = 3  # two chars + terminator
    struct.pack_into("<H", bad, 3, ord("Z"))
    struct.pack_into("<H", bad, 5, ord("Z"))
    assert read_iopcode(bytes(bad)) is None


def test_is_xprocessor5():
    """The mask identifies XProcessor5 bodies, not older ones."""
    assert is_xprocessor5(0x00179500) is True
    assert is_xprocessor5(0xAB179599) is True
    assert is_xprocessor5(0xFF159502) is False  # X-T30, X-Trans IV


def test_unknown_model_falls_back_to_xpro2_baseline():
    """An unidentifiable camera gets only the guaranteed feature set."""
    profile = _profile_with_iopcode("FF179504")  # a full XProcessor5 one
    for model in (None, "X-Wowza9000"):
        caps = capabilities_for(profile, model=model)
        assert caps == BASELINE
        assert caps.has_grain_size is False
        assert caps.has_color_chrome is False
        assert caps.has_clarity is False
        assert caps.has_smooth_skin is False
        assert caps.tone_half_step is False
        assert caps.wb_temp_freeform is False
        assert "Acros" in caps.film_simulations
        assert "Eterna" not in caps.film_simulations


def test_xt3_tier_has_color_chrome_but_not_the_xpro3_features():
    """The X-T3/X-T30 sit between gen 3 and the X-Pro3 feature set."""
    caps = capabilities_for(_profile_with_iopcode("FF159501"), model="X-T3")
    assert caps.has_color_chrome is True
    assert caps.has_grain_size is False
    assert caps.has_clarity is False
    assert "Eterna" in caps.film_simulations
    assert "EternaBleach" not in caps.film_simulations


def test_xe5_tier_offers_everything():
    """A full XProcessor5 body enables all wired features."""
    caps = capabilities_for(_profile_with_iopcode("FF179504"), model="X-E5")
    assert caps.has_grain_size is True
    assert caps.has_color_chrome is True
    assert caps.has_color_chrome_blue is True
    assert caps.has_clarity is True
    assert caps.has_smooth_skin is True
    assert caps.tone_half_step is True
    assert caps.wb_temp_freeform is True
    assert "EternaBleach" in caps.film_simulations


@pytest.mark.parametrize(
    "model",
    ["X-T30 II", "X-T30II", "x-t30 ii", "FUJIFILM X-T30 II"],
)
def test_model_normalization_variants(model: str):
    """Spacing, hyphens, case and vendor prefix do not matter."""
    caps = capabilities_for(_profile_with_iopcode("FF159501"), model=model)
    assert caps.has_clarity is True  # the X-T30 II is an X-T4-class body
    assert caps.has_smooth_skin is False


def test_short_profile_narrows_the_table_row():
    """The profile can only narrow: absent slots disable their features."""
    caps = capabilities_for(b"\x00" * 605, model="X-E5")  # X-T3-length
    assert caps.has_clarity is False
    assert caps.has_color_chrome_blue is False
    assert caps.has_smooth_skin is False
    assert caps.has_grain_size is True  # @545 exists on every profile


def test_half_step_needs_the_table_and_an_xprocessor5_iopcode():
    """Half-step tone requires both the table row and the processor."""
    on_gen4 = capabilities_for(_profile_with_iopcode("FF159501"), model="X-E5")
    assert on_gen4.tone_half_step is False
    on_gen5 = capabilities_for(_profile_with_iopcode("FF179504"), model="X-E5")
    assert on_gen5.tone_half_step is True


def test_film_simulations_gate_per_body():
    """Each tier offers exactly the sims its body has."""
    profile = _profile_with_iopcode("FF179504")

    x100v = capabilities_for(profile, model="X100V").film_simulations
    assert "ClassicNeg" in x100v
    assert "EternaBleach" not in x100v

    xt4 = capabilities_for(profile, model="X-T4").film_simulations
    assert "EternaBleach" in xt4
    assert "NostalgicNeg" not in xt4

    xt5 = capabilities_for(profile, model="X-T5").film_simulations
    assert "NostalgicNeg" in xt5
    assert "RealaAce" not in xt5  # the X-T5 never received Reala Ace

    xe5 = capabilities_for(profile, model="X-E5").film_simulations
    assert "RealaAce" in xe5


def test_tone_range_is_minus_two_to_plus_four_everywhere():
    """The verified tone floor is -2 on every body."""
    for model in ("X-Pro2", "X-T3", "X-E5", None):
        caps = capabilities_for(_profile_with_iopcode("FF179504"), model=model)
        assert (caps.tone_min, caps.tone_max) == (-2, 4)
