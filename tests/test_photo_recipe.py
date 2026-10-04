"""Tests for reading a recipe out of a photo's Fujifilm makernote."""

from __future__ import annotations

from grawji.photo_recipe import TagReader, recipe_from_tags
from grawji.recipe import Recipe


def tags(values: dict[str, str]) -> TagReader:
    """A reader over makernote names or full keys."""
    full = {
        key if key.startswith("Exif.") else f"Exif.Fujifilm.{key}": value
        for key, value in values.items()
    }
    return full.get


def test_an_x_t30_classic_chrome_shot() -> None:
    """The values of the X-T30 sample, read the way exiv2 reports them."""
    found = recipe_from_tags(
        tags(
            {
                "Exif.Image.Model": "X-T30",
                "FilmMode": "1536",
                "Color": "0",
                "Sharpness": "3",
                "HighIsoNoiseReduction": "0",
                "HighlightTone": "0",
                "ShadowTone": "0",
                "WhiteBalance": "0",
                "WhiteBalanceFineTune": "40 -40",
                "GrainEffectRoughness": "0",
                "ColorChromeEffect": "0",
                "DynamicRangeSetting": "0",
                "AutoDynamicRange": "100",
            }
        )
    )
    assert found is not None
    assert found.recipe == Recipe(
        film_simulation="ClassicChrome",
        white_balance="Auto",
        dynamic_range="Auto",
        wb_shift_r=2,
        wb_shift_b=-2,
        origin_body="X-T30",
    )
    assert found.missing == []


def test_half_step_tones_and_strong_chrome() -> None:
    """The GFX100S II half-step shadows, manual DR100."""
    found = recipe_from_tags(
        tags(
            {
                "FilmMode": "2048",
                "Color": "256",
                "Sharpness": "132",
                "HighlightTone": "-32",
                "ShadowTone": "-24",
                "ColorChromeEffect": "64",
                "WhiteBalance": "0",
                "WhiteBalanceFineTune": "0 0",
                "DynamicRangeSetting": "1",
                "DevelopmentDynamicRange": "100",
            }
        )
    )
    assert found is not None
    recipe = found.recipe
    assert recipe.film_simulation == "ClassicNeg"
    assert (recipe.color, recipe.sharpness) == (2, 1)
    assert (recipe.highlights, recipe.shadows) == (2.0, 1.5)
    assert recipe.color_chrome == "Strong"
    assert recipe.dynamic_range == "DR100"


def test_acros_lives_in_the_saturation_tag() -> None:
    """A B&W sim has no FilmMode, and its color stays neutral."""
    found = recipe_from_tags(
        tags(
            {
                "Color": "1280",
                "WhiteBalance": "4080",
                "ColorTemperature": "6300",
                "GrainEffectRoughness": "64",
                "GrainEffectSize": "32",
                "BWAdjustment": "-2",
                "BWMagentaGreen": "3",
            }
        )
    )
    assert found is not None
    recipe = found.recipe
    assert recipe.film_simulation == "Acros"
    assert recipe.color == 0
    assert (recipe.white_balance, recipe.color_temp) == ("Temperature", 6300)
    assert (recipe.grain, recipe.grain_size) == ("Strong", "Large")
    assert (recipe.mono_warm_cool, recipe.mono_magenta_green) == (-2, 3)


def test_clarity_and_a_wide_color_space() -> None:
    """Clarity is stored in thousands, AdobeRGB as uncalibrated."""
    found = recipe_from_tags(
        tags(
            {
                "FilmMode": "2816",
                "Clarity": "-2000",
                "ColorChromeFXBlue": "32",
                "Exif.Photo.ColorSpace": "65535",
            }
        )
    )
    assert found is not None
    recipe = found.recipe
    assert recipe.film_simulation == "RealaAce"
    assert recipe.clarity == -2
    assert recipe.color_chrome_blue == "Weak"
    assert recipe.color_space == "AdobeRGB"
    assert found.missing == ["white_balance", "dynamic_range"]


def test_a_photo_without_a_film_simulation_has_no_recipe() -> None:
    """A FinePix sample with no film sim."""
    assert recipe_from_tags(tags({"WhiteBalance": "512"})) is None
    assert recipe_from_tags(tags({"FilmMode": "garbage"})) is None
