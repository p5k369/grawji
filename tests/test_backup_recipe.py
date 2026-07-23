"""Tests for writing recipes into settings-backup bank slots."""

import pytest

from grawji.backup_recipe import (
    LAYOUTS,
    BackupWriteError,
    apply_checksum,
    layout_for,
    read_names,
    unsupported_fields,
    write_name,
    write_recipe,
    write_recipes,
)
from grawji.recipe import Recipe


def _blank(layout):
    """A zeroed blob of the layout's expected size."""
    return bytes(layout.blob_size)


def _slot_byte(blob, layout, slot, rel_key):
    """Read one field byte of a slot by its relative-offset key."""
    base = layout.sim0 + slot * layout.stride
    return blob[base + layout.rel[rel_key]]


def test_layout_for_known_and_unknown():
    """Known bodies resolve to their layout, unknown ones to None."""
    assert layout_for("X100F") is LAYOUTS["X100F"]
    assert layout_for("FUJIFILM X-T3") is LAYOUTS["XT3"]
    assert layout_for("X-E5") is None
    assert layout_for(None) is None


def test_writes_film_sim_at_bank_offset():
    """The film-sim byte lands at the slot's anchor offset."""
    layout = LAYOUTS["X100F"]
    blob = write_recipe(
        _blank(layout), layout, 0, Recipe(film_simulation="Velvia")
    )
    assert blob[layout.sim0] == 3  # Velvia = 3 on the X100F


def test_slots_are_independent_and_strided():
    """Writing one slot leaves the others untouched (256-byte stride)."""
    layout = LAYOUTS["X100F"]
    blob = _blank(layout)
    blob = write_recipe(
        blob, layout, 2, Recipe(film_simulation="ClassicChrome")
    )
    assert blob[layout.sim0 + 2 * layout.stride] == 13
    assert blob[layout.sim0] == 0  # slot 0 untouched (Provia code 0)


def test_tone_and_nr_encodings():
    """Tones/sharpness encode as 4 - value, NR as value + 4."""
    layout = LAYOUTS["X100F"]
    recipe = Recipe(highlights=4, shadows=-2, sharpness=-4, noise_reduction=-3)
    blob = write_recipe(_blank(layout), layout, 0, recipe)
    assert _slot_byte(blob, layout, 0, "highlight") == 0  # 4 - 4
    assert _slot_byte(blob, layout, 0, "shadow") == 6  # 4 - (-2)
    assert _slot_byte(blob, layout, 0, "sharpness") == 8  # 4 - (-4)
    assert _slot_byte(blob, layout, 0, "nr") == 1  # -3 + 4


def test_x100f_color_lookup_including_inverted_codes():
    """Colour uses the measured lookup, -1/-2 inversion included."""
    layout = LAYOUTS["X100F"]
    for value, code in ((4, 3), (1, 6), (0, 0), (-1, 8), (-2, 7), (-4, 10)):
        blob = write_recipe(_blank(layout), layout, 0, Recipe(color=value))
        assert _slot_byte(blob, layout, 0, "color") == code


def test_grain_and_dr():
    """Grain and dynamic range use their measured enums."""
    layout = LAYOUTS["X100F"]
    recipe = Recipe(grain="Weak", dynamic_range="DR400")
    blob = write_recipe(_blank(layout), layout, 0, recipe)
    assert _slot_byte(blob, layout, 0, "grain") == 1
    assert _slot_byte(blob, layout, 0, "dr") == 3


def test_wb_mode_and_kelvin():
    """Temperature WB writes the mode code plus the Kelvin preset index."""
    layout = LAYOUTS["X100F"]
    recipe = Recipe(white_balance="Temperature", color_temp=5000)
    blob = write_recipe(_blank(layout), layout, 0, recipe)
    assert _slot_byte(blob, layout, 0, "wb_mode") == 8
    assert _slot_byte(blob, layout, 0, "wb_kelvin") == 10  # 5000K, descending


def test_wb_asshot_leaves_wb_untouched():
    """AsShot leaves the bank's stored white balance alone."""
    layout = LAYOUTS["X100F"]
    base = layout.sim0
    start = bytearray(layout.blob_size)
    start[base + layout.rel["wb_mode"]] = 99  # a pre-existing WB code
    blob = write_recipe(
        bytes(start), layout, 0, Recipe(white_balance="AsShot")
    )
    assert _slot_byte(blob, layout, 0, "wb_mode") == 99


def test_color_chrome_only_on_xt3():
    """Color Chrome writes on gen4; gen3 has no such offset."""
    xt3 = LAYOUTS["XT3"]
    blob = write_recipe(_blank(xt3), xt3, 0, Recipe(color_chrome="Strong"))
    assert _slot_byte(blob, xt3, 0, "color_chrome") == 2
    # The X100F layout has no color-chrome offset at all.
    assert "color_chrome" not in LAYOUTS["X100F"].rel


def test_unsupported_values_are_dropped_not_raised():
    """Unsupported values are dropped while the rest still writes."""
    xt3 = LAYOUTS["XT3"]
    # An unmapped film sim and an out-of-range colour are dropped, and the
    # rest of the recipe still writes.
    recipe = Recipe(film_simulation="RealaAce", color=5, sharpness=2)
    blob = write_recipe(_blank(xt3), xt3, 0, recipe)
    dropped = unsupported_fields(xt3, recipe)
    assert any("film simulation" in d for d in dropped)
    assert any("colour" in d for d in dropped)
    # Sharpness (supported) was written: code = 4 - 2 = 2.
    assert _slot_byte(blob, xt3, 0, "sharpness") == 2
    # The dropped film-sim byte was left at its baseline value.
    assert blob[xt3.sim0] == 0


def test_half_step_tone_is_rounded_not_dropped():
    """A half-step tone is rounded to a whole step, not dropped."""
    layout = LAYOUTS["X100F"]
    blob = write_recipe(_blank(layout), layout, 0, Recipe(highlights=1.5))
    assert _slot_byte(blob, layout, 0, "highlight") == 4 - 2  # round(1.5)=2
    assert unsupported_fields(layout, Recipe(highlights=1.5)) == []


def test_bad_slot_and_size_still_raise():
    """Structural errors (bad slot, wrong blob size) still raise."""
    layout = LAYOUTS["X100F"]
    with pytest.raises(BackupWriteError, match="slot"):
        write_recipe(_blank(layout), layout, 7, Recipe())
    with pytest.raises(BackupWriteError, match="expected"):
        write_recipe(b"\x00" * 100, layout, 0, Recipe())


def test_write_recipes_batch():
    """The batch writer patches several slots in one pass."""
    layout = LAYOUTS["X100F"]
    blob = write_recipes(
        _blank(layout),
        layout,
        {
            0: Recipe(film_simulation="Velvia"),
            3: Recipe(film_simulation="Acros"),
        },
    )
    assert blob[layout.sim0] == 3
    assert blob[layout.sim0 + 3 * layout.stride] == 14


def test_name_round_trip_on_named_body():
    """A written bank name reads back; other slots stay empty."""
    layout = LAYOUTS["XT3"]  # gen4 banks are user-nameable
    blob = write_name(_blank(layout), layout, 1, "KODAK")
    names = read_names(blob, layout)
    assert names[1] == "KODAK"
    assert names[0] == ""  # other slots untouched


def test_name_overwrite_clears_old_tail():
    """A shorter name fully replaces a longer previous one."""
    layout = LAYOUTS["XT3"]
    blob = write_name(_blank(layout), layout, 0, "PORTRA")
    blob = write_name(blob, layout, 0, "BW")  # shorter name
    assert read_names(blob, layout)[0] == "BW"


def test_checksum_is_self_consistent_and_gen3_has_none():
    """apply_checksum is idempotent and matches the additive formula."""
    xt3 = LAYOUTS["XT3"]
    blob = write_recipe(_blank(xt3), xt3, 0, Recipe(film_simulation="Velvia"))
    fixed = apply_checksum(blob, xt3.checksum)
    assert apply_checksum(fixed, xt3.checksum) == fixed
    # The stored u16 equals the additive sum + bias over the payload.
    ck = xt3.checksum
    total = sum(fixed[ck.payload_start :]) - sum(
        fixed[ck.offset : ck.offset + ck.skip]
    )
    assert fixed[ck.offset : ck.offset + 2] == (
        (total + ck.bias) & 0xFFFF
    ).to_bytes(2, "little")
    # gen3 has no checksum; apply is a no-op.
    x100f = LAYOUTS["X100F"]
    assert apply_checksum(_blank(x100f), x100f.checksum) == _blank(x100f)


def test_names_unsupported_on_gen3():
    """gen3 has no bank names: read yields [], write raises."""
    layout = LAYOUTS["X100F"]  # no named banks
    assert read_names(_blank(layout), layout) == []
    with pytest.raises(BackupWriteError, match="not user-nameable"):
        write_name(_blank(layout), layout, 0, "X")
