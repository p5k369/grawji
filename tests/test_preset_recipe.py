"""Tests for the gen5 preset-property recipe encoder."""

import pytest

from grawji.backup_recipe import BackupWriteError
from grawji.preset_recipe import (
    PASSTHROUGH_DEFAULTS,
    PROP_CLARITY,
    PROP_COLOR,
    PROP_COLOR_CHROME,
    PROP_COLOR_CHROME_BLUE,
    PROP_DYNAMIC_RANGE,
    PROP_FILM_SIMULATION,
    PROP_GRAIN,
    PROP_HIGHLIGHT,
    PROP_IMAGE_SIZE,
    PROP_MONO_MG,
    PROP_MONO_WC,
    PROP_NOISE_REDUCTION,
    PROP_SHADOW,
    PROP_SHARPNESS,
    PROP_SMOOTH_SKIN,
    PROP_WB_COLOR_TEMP,
    PROP_WB_SHIFT_B,
    PROP_WB_SHIFT_R,
    PROP_WHITE_BALANCE,
    UNSET_SENTINEL,
    encode_recipe,
    readback_matches,
    unsupported_fields,
)
from grawji.recipe import Recipe


def _as_dict(props: list[tuple[int, int]]) -> dict[int, int]:
    return dict(props)


def test_encodes_the_d185_compatible_codes():
    """Film sim, chrome, grain, NR and tones use the verified codes."""
    recipe = Recipe(
        film_simulation="Velvia",
        dynamic_range="DR400",
        grain="Weak",
        grain_size="Large",
        color_chrome="Strong",
        color_chrome_blue="Weak",
        smooth_skin="Off",
        highlights=1.5,
        shadows=-2,
        color=3,
        sharpness=-1,
        noise_reduction=-4,
        clarity=5,
    )
    got = _as_dict(encode_recipe(recipe))
    assert got[PROP_FILM_SIMULATION] == 2  # Velvia in the rawji enum
    assert got[PROP_DYNAMIC_RANGE] == 400  # raw percentage, not an index
    assert got[PROP_GRAIN] == 4  # Weak/Large in the flat enum
    assert got[PROP_COLOR_CHROME] == 3
    assert got[PROP_COLOR_CHROME_BLUE] == 2
    assert got[PROP_SMOOTH_SKIN] == 1
    assert got[PROP_HIGHLIGHT] == 15  # value*10, half steps supported
    assert got[PROP_SHADOW] == (-20) & 0xFFFF
    assert got[PROP_COLOR] == 30
    assert got[PROP_SHARPNESS] == (-10) & 0xFFFF
    assert got[PROP_NOISE_REDUCTION] == 0x8000  # the NR code table
    assert got[PROP_CLARITY] == 50


def test_wb_temperature_follows_the_wb_mode():
    """The color temperature comes immediately after the WB mode."""
    recipe = Recipe(
        white_balance="Temperature",
        color_temp=5150,
        wb_shift_r=3,
        wb_shift_b=-6,
    )
    props = encode_recipe(recipe)
    order = [prop for prop, _ in props]
    at = order.index(PROP_WHITE_BALANCE)
    assert order[at + 1] == PROP_WB_COLOR_TEMP
    got = _as_dict(props)
    assert got[PROP_WHITE_BALANCE] == 0x8007
    assert got[PROP_WB_COLOR_TEMP] == 5150
    assert got[PROP_WB_SHIFT_R] == 3
    assert got[PROP_WB_SHIFT_B] == (-6) & 0xFFFF


def test_wb_mode_without_temperature_omits_the_kelvin_prop():
    """A non-Temperature WB never writes the color temperature."""
    got = _as_dict(encode_recipe(Recipe(white_balance="Daylight")))
    assert got[PROP_WHITE_BALANCE] == 0x0004
    assert PROP_WB_COLOR_TEMP not in got


def test_as_shot_echoes_the_slot_wb_or_omits_the_cluster():
    """An AsShot WB preserves the slot's stored values on a full write."""
    base = {
        PROP_WHITE_BALANCE: 0x8007,
        PROP_WB_COLOR_TEMP: 6500,
        PROP_WB_SHIFT_R: 2,
        PROP_WB_SHIFT_B: 8,
    }
    got = _as_dict(encode_recipe(Recipe(), base))
    assert got[PROP_WHITE_BALANCE] == 0x8007
    assert got[PROP_WB_COLOR_TEMP] == 6500
    assert got[PROP_WB_SHIFT_R] == 2
    assert got[PROP_WB_SHIFT_B] == 8
    # Unreadable slot: the WB cluster is omitted, the stored values stand.
    got = _as_dict(encode_recipe(Recipe()))
    assert PROP_WHITE_BALANCE not in got
    assert PROP_WB_SHIFT_R not in got


def test_mono_sims_swap_color_for_the_toning_axes():
    """Acros/Monochrome write WC/MG (when set) and never color."""
    recipe = Recipe(
        film_simulation="AcrosYe", mono_warm_cool=4, mono_magenta_green=-2
    )
    got = _as_dict(encode_recipe(recipe))
    assert PROP_COLOR not in got
    assert got[PROP_MONO_WC] == 40
    assert got[PROP_MONO_MG] == (-20) & 0xFFFF
    # A zero axis is omitted: the camera rejects a write of 0.
    got = _as_dict(encode_recipe(Recipe(film_simulation="Monochrome")))
    assert PROP_MONO_WC not in got
    assert PROP_MONO_MG not in got


def test_sepia_locks_color_but_takes_no_toning():
    """Sepia omits color and ignores stray mono toning values."""
    recipe = Recipe(film_simulation="Sepia", mono_warm_cool=5, color=2)
    got = _as_dict(encode_recipe(recipe))
    assert PROP_COLOR not in got
    assert PROP_MONO_WC not in got


def test_color_sims_never_write_the_toning_axes():
    """A color sim's WC/MG slots hold WB data and must stay untouched."""
    recipe = Recipe(film_simulation="Provia", mono_warm_cool=5)
    got = _as_dict(encode_recipe(recipe))
    assert PROP_MONO_WC not in got
    assert got[PROP_COLOR] == 0


def test_passthrough_props_echo_the_base_or_fall_back():
    """Unknown/non-recipe properties echo the slot's current values."""
    base = {PROP_IMAGE_SIZE: 2}
    got = _as_dict(encode_recipe(Recipe(), base))
    assert got[PROP_IMAGE_SIZE] == 2
    got = _as_dict(encode_recipe(Recipe()))
    for prop, default in PASSTHROUGH_DEFAULTS.items():
        assert got[prop] == default


def test_unknown_enum_values_raise():
    """Values with no verified code refuse to encode."""
    with pytest.raises(BackupWriteError, match="film simulation"):
        encode_recipe(Recipe(film_simulation="Kodachrome"))
    with pytest.raises(BackupWriteError, match="white balance"):
        encode_recipe(Recipe(white_balance="Moonlight"))
    with pytest.raises(BackupWriteError, match="dynamic range"):
        encode_recipe(Recipe(dynamic_range="DR800"))


def test_unsupported_fields_reports_exposure_only():
    """Exposure compensation is the one recipe field presets lack."""
    assert unsupported_fields(Recipe()) == []
    assert unsupported_fields(Recipe(exposure=1.0)) == [
        "exposure compensation"
    ]


def test_readback_matches_accepts_the_storage_quirks():
    """Grain Off 1->6 and the 0x8000 unset sentinel count as applied."""
    assert readback_matches(PROP_FILM_SIMULATION, 2, 2)
    assert not readback_matches(PROP_FILM_SIMULATION, 2, 3)
    assert readback_matches(PROP_GRAIN, 1, 6)
    assert readback_matches(PROP_GRAIN, 1, 7)  # Off after Large (X-E5)
    assert not readback_matches(PROP_GRAIN, 2, 6)
    assert readback_matches(PROP_NOISE_REDUCTION, 0x1000, UNSET_SENTINEL)
    assert readback_matches(PROP_HIGHLIGHT, 15, UNSET_SENTINEL)
    assert not readback_matches(PROP_FILM_SIMULATION, 2, UNSET_SENTINEL)
