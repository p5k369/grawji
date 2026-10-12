"""Tests for the d185 profile patching of a transplant render."""

import struct

import pytest
import rawji

from grawji.transplant.carriers import select_carrier
from grawji.transplant.profile_patch import patch_ev, patch_profile

_FILM_SIM = 541
_WB = 557
_SHIFT_R = 561
_SHIFT_B = 565
_NEUTRALIZED = (561, 565, 569, 573, 577, 581, 585)


def _profile() -> bytes:
    """A fake 629-byte profile with recognizable carrier settings."""
    prof = bytearray(629)
    struct.pack_into("<I", prof, _FILM_SIM, 15)
    struct.pack_into("<I", prof, _WB, 2)
    for offset in _NEUTRALIZED:
        struct.pack_into("<i", prof, offset, 30)
    return bytes(prof)


def test_patch_sets_film_sim():
    """A named film simulation lands as its rawji code at 541."""
    prof = patch_profile(_profile(), film_sim="Eterna")
    code = struct.unpack_from("<I", prof, _FILM_SIM)[0]
    assert code == int(rawji.FilmSimulation["Eterna"])


def test_patch_keeps_carrier_sim_without_film_sim():
    """No film_sim keeps the carrier shot's simulation byte."""
    prof = patch_profile(_profile())
    assert struct.unpack_from("<I", prof, _FILM_SIM)[0] == 15


def test_neutralize_resets_bleed_through():
    """Neutralizing zeroes the carrier's shift/temp/tone settings."""
    prof = patch_profile(_profile())
    for offset in _NEUTRALIZED:
        assert struct.unpack_from("<i", prof, offset)[0] == 0
    wb = struct.unpack_from("<I", prof, _WB)[0]
    assert wb == int(rawji.WhiteBalance["Daylight"])


def test_keep_recipe_leaves_carrier_settings():
    """neutralize=False leaves the profile untouched."""
    prof = patch_profile(_profile(), neutralize=False)
    assert prof == _profile()


def test_wb_shift_applied_after_neutralize():
    """A WB shift survives the neutralize pass."""
    prof = patch_profile(_profile(), wb_shift=(2, -2))
    assert struct.unpack_from("<i", prof, _SHIFT_R)[0] == 2
    assert struct.unpack_from("<i", prof, _SHIFT_B)[0] == -2


def test_patch_does_not_mutate_input():
    """patch_profile returns a copy and leaves its input alone."""
    original = _profile()
    patch_profile(original, film_sim="Provia")
    assert original == _profile()


def test_patch_ev_encodes_thousandths():
    """The EV offset is stored as round(EV*1000) at 529."""
    prof = patch_ev(_profile(), -1.67)
    assert struct.unpack_from("<i", prof, 529)[0] == -1670


def test_patch_ev_rejects_values_the_engine_ignores():
    """Below -2 the engine falls back to 0 EV, so writing it raises."""
    with pytest.raises(ValueError, match="honored"):
        patch_ev(_profile(), -2.33)
    with pytest.raises(ValueError, match="honored"):
        patch_ev(_profile(), 3.33)
