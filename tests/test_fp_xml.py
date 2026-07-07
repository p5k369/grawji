"""Tests for X RAW Studio FP recipe import."""

from __future__ import annotations

import pytest

from grawji.fp_xml import parse_fp, serialize_fp
from grawji.recipe import Recipe

# A real X-T3 FP1 sample (petabyt/fp, "Kodachrome 64").
KODACHROME_FP1 = """<?xml version="1.0" encoding="utf-8"?>
<ConversionProfile application="XRFC" version="1.10.0.0">
    <PropertyGroup device="X-T3" version="X-T3_0100" label="Kodachrome 64">
        <IOPCode>FF159501</IOPCode>
        <ExposureBias>0</ExposureBias>
        <DynamicRange>100</DynamicRange>
        <FilmSimulation>Classic</FilmSimulation>
        <GrainEffect>STRONG</GrainEffect>
        <ChromeEffect>WEAK</ChromeEffect>
        <WhiteBalance>Auto</WhiteBalance>
        <WBShiftR>2</WBShiftR>
        <WBShiftB>-5</WBShiftB>
        <WBColorTemp>10000K</WBColorTemp>
        <HighlightTone>0</HighlightTone>
        <ShadowTone>0</ShadowTone>
        <Color>2</Color>
        <Sharpness>1</Sharpness>
        <NoisReduction>-4</NoisReduction>
        <ColorSpace>sRGB</ColorSpace>
    </PropertyGroup>
</ConversionProfile>
"""


def test_parses_real_sample() -> None:
    recipe = parse_fp(KODACHROME_FP1)
    assert recipe.film_simulation == "ClassicChrome"
    assert recipe.grain == "Strong"
    assert recipe.color_chrome == "Weak"
    assert recipe.white_balance == "Auto"
    assert recipe.wb_shift_r == 2
    assert recipe.wb_shift_b == -5
    assert recipe.color == 2
    assert recipe.sharpness == 1
    assert recipe.noise_reduction == -4
    assert recipe.color_space == "sRGB"
    assert recipe.exposure == 0.0


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("0", 0.0),
        ("P0P33", 1 / 3),
        ("P0P67", 2 / 3),
        ("M0P67", -2 / 3),
        ("P2P33", 7 / 3),
        ("P2P0", 2.0),
        ("M3P00", -3.0),
    ],
)
def test_exposure_tokens(token: str, expected: float) -> None:
    recipe = _with(f"<ExposureBias>{token}</ExposureBias>")
    assert recipe.exposure == pytest.approx(expected)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("Classic", "ClassicChrome"),
        ("BW", "Monochrome"),
        ("NEGAStd", "ProNegStd"),
        ("NEGAhi", "ProNegHi"),
        ("Acros", "Acros"),
        ("ClassicNEGA", "ClassicNeg"),
        ("NostalgicNEGA", "NostalgicNeg"),
        ("RealaACE", "RealaAce"),
        ("BleachBypass", "EternaBleach"),
    ],
)
def test_film_simulation_vocabulary(token: str, expected: str) -> None:
    recipe = _with(f"<FilmSimulation>{token}</FilmSimulation>")
    assert recipe.film_simulation == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [("OFF", "Off"), ("WEAK", "Weak"), ("STRONG", "Strong")],
)
def test_color_chrome_vocabulary(token: str, expected: str) -> None:
    recipe = _with(f"<ChromeEffect>{token}</ChromeEffect>")
    assert recipe.color_chrome == expected


def test_color_chrome_serializes_to_chrome_effect() -> None:
    out = serialize_fp(Recipe(color_chrome="Strong"))
    assert "<ChromeEffect>STRONG</ChromeEffect>" in out


def test_invalid_white_balance_means_as_shot() -> None:
    assert _with("<WhiteBalance>INVALID</WhiteBalance>").white_balance == (
        "AsShot"
    )
    assert _with("<WhiteBalance>FLight1</WhiteBalance>").white_balance == (
        "Fluorescent1"
    )


def test_color_temp_strips_kelvin_suffix() -> None:
    assert _with("<WBColorTemp>5900K</WBColorTemp>").color_temp == 5900


def test_zero_color_temp_keeps_default() -> None:
    assert _with("<WBColorTemp>0K</WBColorTemp>").color_temp == (
        Recipe().color_temp
    )


def test_unknown_tokens_keep_defaults() -> None:
    recipe = _with("<FilmSimulation>SomethingNew</FilmSimulation>")
    assert recipe.film_simulation == Recipe().film_simulation


def test_byte_order_mark_is_tolerated() -> None:
    recipe = parse_fp("﻿" + KODACHROME_FP1)
    assert recipe.film_simulation == "ClassicChrome"


def test_malformed_xml_raises() -> None:
    with pytest.raises(ValueError, match="malformed XML"):
        parse_fp("<ConversionProfile><not closed")


def test_wrong_root_raises() -> None:
    with pytest.raises(ValueError, match="ConversionProfile"):
        parse_fp("<Something/>")


@pytest.mark.parametrize(
    "recipe",
    [
        Recipe(),
        Recipe(
            film_simulation="ClassicChrome",
            white_balance="Auto",
            dynamic_range="DR200",
            grain="Strong",
            grain_size="Large",
            color_chrome="Weak",
            color_chrome_blue="Strong",
            smooth_skin="Weak",
            clarity=-3,
            exposure=2 / 3,
            highlights=2,
            shadows=-2,
            color=3,
            sharpness=-4,
            noise_reduction=-2,
            wb_shift_r=4,
            wb_shift_b=-5,
            color_space="AdobeRGB",
        ),
        Recipe(
            film_simulation="Monochrome",
            white_balance="Temperature",
            color_temp=5900,
            exposure=-1 / 3,
            mono_warm_cool=6,
            mono_magenta_green=-4,
        ),
    ],
)
def test_round_trip(recipe: Recipe) -> None:
    parsed = parse_fp(serialize_fp(recipe))
    assert parsed.film_simulation == recipe.film_simulation
    assert parsed.white_balance == recipe.white_balance
    assert parsed.dynamic_range == recipe.dynamic_range
    assert parsed.grain == recipe.grain
    assert parsed.grain_size == recipe.grain_size
    assert parsed.color_chrome == recipe.color_chrome
    assert parsed.color_chrome_blue == recipe.color_chrome_blue
    assert parsed.smooth_skin == recipe.smooth_skin
    assert parsed.clarity == recipe.clarity
    assert parsed.exposure == pytest.approx(recipe.exposure)
    assert parsed.highlights == recipe.highlights
    assert parsed.shadows == recipe.shadows
    assert parsed.color == recipe.color
    assert parsed.sharpness == recipe.sharpness
    assert parsed.noise_reduction == recipe.noise_reduction
    assert parsed.wb_shift_r == recipe.wb_shift_r
    assert parsed.wb_shift_b == recipe.wb_shift_b
    assert parsed.color_space == recipe.color_space
    assert parsed.mono_warm_cool == recipe.mono_warm_cool
    assert parsed.mono_magenta_green == recipe.mono_magenta_green


def test_mono_color_dropped_for_colour_sims() -> None:
    velvia = Recipe(
        film_simulation="Velvia", mono_warm_cool=8, mono_magenta_green=-8
    )
    parsed = parse_fp(serialize_fp(velvia))
    assert parsed.mono_warm_cool == 0
    assert parsed.mono_magenta_green == 0


def test_color_temp_only_round_trips_in_temperature_mode() -> None:
    # Outside Temperature mode the kelvin is written as "0K" and dropped.
    daylight = Recipe(white_balance="Daylight", color_temp=5900)
    assert parse_fp(serialize_fp(daylight)).color_temp == Recipe().color_temp
    temp = Recipe(white_balance="Temperature", color_temp=5900)
    assert parse_fp(serialize_fp(temp)).color_temp == 5900


def test_iopcode_is_eight_hex_digits() -> None:
    assert "<IOPCode>FF159501</IOPCode>" in serialize_fp(
        Recipe(), iopcode=0xFF159501
    )


def test_new_film_sims_round_trip() -> None:
    for name in ("ClassicNeg", "NostalgicNeg", "RealaAce", "EternaBleach"):
        out = serialize_fp(Recipe(film_simulation=name))
        assert parse_fp(out).film_simulation == name


def _with(field_xml: str) -> Recipe:
    """Parse a minimal FP document carrying a single field."""
    return parse_fp(
        '<?xml version="1.0" encoding="utf-8"?>'
        "<ConversionProfile><PropertyGroup>"
        f"{field_xml}"
        "</PropertyGroup></ConversionProfile>"
    )
