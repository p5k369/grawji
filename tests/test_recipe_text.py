"""Tests for the community text-recipe parser."""

from __future__ import annotations

from grawji.recipe_text import parse_recipe_text

_KODACHROME = """Kodachrome 64
Film Simulation: Classic Chrome
Grain Effect: Weak, Small
Color Chrome Effect: Strong
Color Chrome FX Blue: Weak
White Balance: Daylight, +2 Red & -5 Blue
Dynamic Range: DR200
Highlight: +1
Shadow: +1.5
Color: +2
Sharpness: -1
Noise Reduction: -4
Clarity: -2
ISO: Auto, up to ISO 6400
Exposure Compensation: +1/3 to +2/3 (typically)
"""


def test_full_fuji_x_weekly_recipe():
    """A complete recipe parses with title and no notes."""
    parsed = parse_recipe_text(_KODACHROME)
    assert parsed is not None
    assert parsed.title == "Kodachrome 64"
    assert parsed.notes == []
    recipe = parsed.recipe
    assert recipe.film_simulation == "ClassicChrome"
    assert recipe.grain == "Weak"
    assert recipe.grain_size == "Small"
    assert recipe.color_chrome == "Strong"
    assert recipe.color_chrome_blue == "Weak"
    assert recipe.white_balance == "Daylight"
    assert (recipe.wb_shift_r, recipe.wb_shift_b) == (2, -5)
    assert recipe.dynamic_range == "DR200"
    assert recipe.highlights == 1.0
    assert recipe.shadows == 1.5
    assert recipe.color == 2
    assert recipe.sharpness == -1
    assert recipe.noise_reduction == -4
    assert recipe.clarity == -2
    assert recipe.exposure == 0.0


def test_kelvin_white_balance():
    """A kelvin WB maps to Temperature plus the color temp."""
    parsed = parse_recipe_text(
        "White Balance: 7100K, -2 Red & -2 Blue\nColor: +1"
    )
    assert parsed is not None
    assert parsed.recipe.white_balance == "Temperature"
    assert parsed.recipe.color_temp == 7100
    assert (parsed.recipe.wb_shift_r, parsed.recipe.wb_shift_b) == (-2, -2)


def test_auto_white_priority_becomes_auto_with_note():
    """Capture-time auto-WB biases fall back to plain Auto."""
    parsed = parse_recipe_text("White Balance: Auto White Priority")
    assert parsed is not None
    assert parsed.recipe.white_balance == "Auto"
    assert any("capture-time" in note for note in parsed.notes)


def test_film_sim_spellings():
    """Common community spellings resolve to the rawji names."""
    cases = {
        "Classic Negative": "ClassicNeg",
        "PRO Neg. Hi": "ProNegHi",
        "Nostalgic Neg.": "NostalgicNeg",
        "Eterna Bleach Bypass": "EternaBleach",
        "ACROS+R": "AcrosR",
        "Acros (+Ye Filter)": "AcrosYe",
        "Reala Ace": "RealaAce",
    }
    for spelled, expected in cases.items():
        parsed = parse_recipe_text(f"Film Simulation: {spelled}")
        assert parsed is not None, spelled
        assert parsed.recipe.film_simulation == expected, spelled


def test_dr_auto_is_a_first_class_value():
    """DR Auto parses as "Auto"."""
    parsed = parse_recipe_text("Dynamic Range: DR-Auto\nColor: 0")
    assert parsed is not None
    assert parsed.recipe.dynamic_range == "Auto"
    assert parsed.notes == []


def test_monochromatic_color():
    """WC/MG toning parses in the labeled spelling."""
    parsed = parse_recipe_text(
        "Film Simulation: Acros\nMonochromatic Color: +2 WC & -3 MG"
    )
    assert parsed is not None
    assert parsed.recipe.mono_warm_cool == 2
    assert parsed.recipe.mono_magenta_green == -3


def test_british_colour_spellings():
    """British "Colour" labels fold to their American handler keys."""
    parsed = parse_recipe_text(
        "Film Simulation    Acros+R\n"
        "Grain Effect    Strong / Large\n"
        "Monochromatic Colour    WC: -1, MG: 0\n"
        "Highlights    +4\n"
        "Shadows    +4\n"
        "Sharpness    +1\n"
        "Noise Reduction    -4\n"
        "Clarity    +3"
    )
    assert parsed is not None
    assert parsed.notes == []
    recipe = parsed.recipe
    assert recipe.film_simulation == "AcrosR"
    assert recipe.grain == "Strong"
    assert recipe.grain_size == "Large"
    assert recipe.mono_warm_cool == -1
    assert recipe.mono_magenta_green == 0
    assert recipe.highlights == 4.0
    assert recipe.shadows == 4.0
    assert recipe.sharpness == 1
    assert recipe.noise_reduction == -4
    assert recipe.clarity == 3


def test_unset_wb_and_dr_default_to_auto():
    """A paste without WB or DR lines means Auto, not AsShot/DR100."""
    parsed = parse_recipe_text("Film Simulation: Classic Chrome")
    assert parsed is not None
    assert parsed.recipe.white_balance == "Auto"
    assert parsed.recipe.dynamic_range == "Auto"


def test_explicit_wb_and_dr_beat_the_auto_default():
    """Stated WB and DR values still win over the paste defaults."""
    parsed = parse_recipe_text(
        "Film Simulation: Classic Chrome\n"
        "White Balance: Daylight\n"
        "Dynamic Range: DR400"
    )
    assert parsed is not None
    assert parsed.recipe.white_balance == "Daylight"
    assert parsed.recipe.dynamic_range == "DR400"


def test_colour_saturation_label():
    """A bare "Colour" label routes to the color value."""
    parsed = parse_recipe_text(
        "Film Simulation: Classic Chrome\n"
        "Colour: +2\n"
        "Colour Chrome FX Blue: Weak"
    )
    assert parsed is not None
    assert parsed.recipe.color == 2
    assert parsed.recipe.color_chrome_blue == "Weak"


def test_toning_single_value_is_warm_cool():
    """An old-style single Toning value lands on warm-cool."""
    parsed = parse_recipe_text("Film Simulation: Acros\nToning: +1 (warm)")
    assert parsed is not None
    assert parsed.recipe.mono_warm_cool == 1


def test_unknown_labels_are_noted():
    """Unrecognized lines become notes instead of failures."""
    parsed = parse_recipe_text("Color: +2\nTele-Converter: 1.4x")
    assert parsed is not None
    assert parsed.recipe.color == 2
    assert any("teleconverter" in note for note in parsed.notes)


_PROSE_STYLE = """Film simulation: Classic Negative
Dynamic Range: DR200
Auto-WB Shift: +4 Red, -4 Blue
Highlights: +1
Shadows: +0
Color: +3 (sometimes I\u2019ll drop it to +1 when I want a less saturated look)
Noise reduction: -4
Clarity: 0
Sharpening: 0
Grain effect: Strong
Grain size: Large
Color chrome fx: Off
Color chrome blue: Weak
Exposure compensation: typically between +1/3 and +1
"""

_TABLE_STYLE = """Film simulation
Classic Negative
DR Auto
Dynamic range
Grain Effect
Small / Weak
Color Chrome Effect
Weak
Color Chrome FX Blue
Weak
White Balance
K6500, +3 Red, -2 Blue
ISO
Auto up to ISO 1600
Exposure Comp.
0 to +1
Adjustments
-1
Highlight
-0.5
Shadow
+1
Color
-1
Sharpness
-4
Noise Red.
0
Clarity
"""


def test_prose_style_recipe():
    """Colon lines with commentary, alias labels and pair shifts."""
    parsed = parse_recipe_text(_PROSE_STYLE)
    assert parsed is not None
    recipe = parsed.recipe
    assert recipe.film_simulation == "ClassicNeg"
    assert recipe.dynamic_range == "DR200"
    assert (recipe.wb_shift_r, recipe.wb_shift_b) == (4, -4)
    assert recipe.highlights == 1.0
    assert recipe.shadows == 0.0
    assert recipe.color == 3
    assert recipe.noise_reduction == -4
    assert recipe.grain == "Strong"
    assert recipe.grain_size == "Large"
    assert recipe.color_chrome == "Off"
    assert recipe.color_chrome_blue == "Weak"


def test_table_style_recipe():
    """Label and value on separate lines, in either order."""
    parsed = parse_recipe_text(_TABLE_STYLE)
    assert parsed is not None
    recipe = parsed.recipe
    assert recipe.film_simulation == "ClassicNeg"
    assert recipe.dynamic_range == "Auto"
    assert recipe.grain == "Weak"
    assert recipe.grain_size == "Small"
    assert recipe.color_chrome == "Weak"
    assert recipe.color_chrome_blue == "Weak"
    assert recipe.white_balance == "Temperature"
    assert recipe.color_temp == 6500
    assert (recipe.wb_shift_r, recipe.wb_shift_b) == (3, -2)
    assert recipe.highlights == -1.0
    assert recipe.shadows == -0.5
    assert recipe.color == 1
    assert recipe.sharpness == -1
    assert recipe.noise_reduction == -4
    assert recipe.clarity == 0


def test_tab_separated_table_lines():
    """Web-table pastes with tab or column gaps parse as pairs."""
    parsed = parse_recipe_text(
        "Film Simulation\tClassic Chrome\n"
        "Grain Effect\tWeak, Small\n"
        "Highlight    +2\n"
        "Shadow\t-1"
    )
    assert parsed is not None
    assert parsed.recipe.film_simulation == "ClassicChrome"
    assert parsed.recipe.grain == "Weak"
    assert parsed.recipe.highlights == 2.0
    assert parsed.recipe.shadows == -1.0


def test_german_recipe():
    """German labels and value words resolve too."""
    parsed = parse_recipe_text(
        "Filmsimulation: Classic Chrome\n"
        "Weißabgleich: Tageslicht, R+4 B-4\n"
        "Dynamikbereich: DR200\n"
        "Körnung: Stark, Groß\n"
        "Lichter: -1\n"
        "Schatten: +2\n"
        "Farbe: +3\n"
        "Schärfe: -2\n"
        "Rauschreduzierung: -4\n"
        "Klarheit: +1"
    )
    assert parsed is not None
    recipe = parsed.recipe
    assert recipe.film_simulation == "ClassicChrome"
    assert recipe.white_balance == "Daylight"
    assert (recipe.wb_shift_r, recipe.wb_shift_b) == (4, -4)
    assert recipe.dynamic_range == "DR200"
    assert recipe.grain == "Strong"
    assert recipe.grain_size == "Large"
    assert recipe.highlights == -1.0
    assert recipe.shadows == 2.0
    assert recipe.color == 3
    assert recipe.sharpness == -2
    assert recipe.noise_reduction == -4
    assert recipe.clarity == 1


def test_letter_form_wb_shift():
    """Shorthand shifts like "R+2, B-5" parse without the words."""
    parsed = parse_recipe_text("WB Shift: R+2, B-5")
    assert parsed is not None
    assert (parsed.recipe.wb_shift_r, parsed.recipe.wb_shift_b) == (2, -5)


_FRUIT_PASTEL = """Fruit Pastel
Velvia
Dynamic Range: 400
Highlight: 0
Shadow: -2
Color: +1
Noise Reduction: -4
Sharpening: -2
Clarity: 0
Grain Effect: Strong, Large
Color Chrome Effect: Strong
Color Chrome Effect Blue: Strong
White Balance: Auto (R3 B-3)
"""

_GFX_GERMAN = """Filmsimulation    Acros + Gelb
Monochrome Farbe    WC: 0 MD: 0
K\u00f6rnungseffekt    aus
Wei\u00dfabgleich    Tageslicht
WA verschieben    R: -8 B: -9
Ton Lichter    +1
Schattier. Ton    +3
Sch\u00e4rfe    +2
Hohe ISO-NR    -2
Klarheit    0
Push/Pull    + 1/3 EBV
"""


def test_fruit_pastel_recipe():
    """A bare sim line, parenthesized WB shifts, unsigned R3."""
    parsed = parse_recipe_text(_FRUIT_PASTEL)
    assert parsed is not None
    assert parsed.title == "Fruit Pastel"
    recipe = parsed.recipe
    assert recipe.film_simulation == "Velvia"
    assert recipe.dynamic_range == "DR400"
    assert recipe.highlights == 0.0
    assert recipe.shadows == -2.0
    assert recipe.color == 1
    assert recipe.noise_reduction == -4
    assert recipe.sharpness == -2
    assert recipe.grain == "Strong"
    assert recipe.grain_size == "Large"
    assert recipe.color_chrome == "Strong"
    assert recipe.color_chrome_blue == "Strong"
    assert recipe.white_balance == "Auto"
    assert (recipe.wb_shift_r, recipe.wb_shift_b) == (3, -3)


def test_gfx_german_table_recipe():
    """German table labels with colons inside the value column."""
    parsed = parse_recipe_text(_GFX_GERMAN)
    assert parsed is not None
    recipe = parsed.recipe
    assert recipe.film_simulation == "AcrosYe"
    assert recipe.mono_warm_cool == 0
    assert recipe.mono_magenta_green == 0
    assert recipe.grain == "Off"
    assert recipe.white_balance == "Daylight"
    assert (recipe.wb_shift_r, recipe.wb_shift_b) == (-8, -9)
    assert recipe.highlights == 1.0
    assert recipe.shadows == 3.0
    assert recipe.sharpness == 2
    assert recipe.noise_reduction == -2
    assert recipe.clarity == 0
    assert recipe.exposure == 0.0  # Push/Pull is capture-time


_X_WEEKLY_XTRANS5 = """Pacific Blues
Film Simulation: Classic Negative
Grain Effect: Weak, Large
Color Chrome Effect: Strong
Color Chrome FX Blue: Weak
White Balance: Auto White Priority, +2 Red & -6 Blue
Dynamic Range: DR400
D Range Priority: Off
Highlight: -1.5
Shadow: +2
Color: +3
Sharpness: -2
High ISO NR: -4
Clarity: -2
ISO: Auto, up to ISO 6400
Exposure Compensation: +1/3 to +1 (typically)
"""

_X_WEEKLY_BW = """Moody Monochrome
Film Simulation: Acros+R
Grain Effect: Strong, Large
Color Chrome Effect: Off
Color Chrome FX Blue: Off
White Balance: 9100K, -9 Red & +9 Blue
Dynamic Range: DR200
Highlight: +3
Shadow: +4
Toning: WC +2, MG 0
Sharpness: 0
High ISO NR: -4
Clarity: -4
ISO: Auto, up to ISO 12800
Exposure Compensation: +2/3 to +1 1/3 (typically)
"""


def test_x_weekly_xtrans5_reference():
    """The canonical current Fuji X Weekly layout stays fully parsed."""
    parsed = parse_recipe_text(_X_WEEKLY_XTRANS5)
    assert parsed is not None
    assert parsed.title == "Pacific Blues"
    recipe = parsed.recipe
    assert recipe.film_simulation == "ClassicNeg"
    assert recipe.grain == "Weak"
    assert recipe.grain_size == "Large"
    assert recipe.color_chrome == "Strong"
    assert recipe.color_chrome_blue == "Weak"
    assert recipe.white_balance == "Auto"
    assert (recipe.wb_shift_r, recipe.wb_shift_b) == (2, -6)
    assert recipe.dynamic_range == "DR400"
    assert recipe.highlights == -1.5
    assert recipe.shadows == 2.0
    assert recipe.color == 3
    assert recipe.sharpness == -2
    assert recipe.noise_reduction == -4
    assert recipe.clarity == -2
    assert recipe.exposure == 0.0


def test_x_weekly_bw_reference():
    """A kelvin B&W X Weekly recipe with WC/MG toning stays parsed."""
    parsed = parse_recipe_text(_X_WEEKLY_BW)
    assert parsed is not None
    assert parsed.title == "Moody Monochrome"
    recipe = parsed.recipe
    assert recipe.film_simulation == "AcrosR"
    assert recipe.white_balance == "Temperature"
    assert recipe.color_temp == 9100
    assert (recipe.wb_shift_r, recipe.wb_shift_b) == (-9, 9)
    assert recipe.mono_warm_cool == 2
    assert recipe.mono_magenta_green == 0
    assert recipe.highlights == 3.0
    assert recipe.shadows == 4.0
    assert recipe.noise_reduction == -4


def test_sim_titled_recipe_keeps_its_title():
    """A recipe literally named "Velvia" keeps the name as title."""
    parsed = parse_recipe_text("Velvia\nFilm Simulation: Astia\nColor: +2")
    assert parsed is not None
    assert parsed.title == "Velvia"
    assert parsed.recipe.film_simulation == "Astia"


def test_unrecognizable_text_returns_none():
    """Prose without any recipe field is rejected."""
    assert parse_recipe_text("hello world\nno recipe here") is None
    assert parse_recipe_text("") is None


def test_bulleted_lines_parse_too():
    """Bullet or dash prefixes do not break the label split."""
    parsed = parse_recipe_text("• Highlight: -2\n- Shadow: +2")
    assert parsed is not None
    assert parsed.recipe.highlights == -2.0
    assert parsed.recipe.shadows == 2.0
