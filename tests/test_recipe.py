"""Tests for the pure Recipe data model."""

from grawji.recipe import Recipe


def test_defaults():
    """A fresh recipe defaults to neutral Provia / AsShot / DR100."""
    recipe = Recipe()
    assert recipe.film_simulation == "Provia"
    assert recipe.white_balance == "AsShot"
    assert recipe.dynamic_range == "DR100"
    assert recipe.highlights == 0
    assert recipe.shadows == 0
    assert recipe.color == 0
    assert recipe.sharpness == 0


def test_roundtrip_dict():
    """to_dict/from_dict round-trips a fully-populated recipe."""
    recipe = Recipe(
        film_simulation="Velvia",
        white_balance="Daylight",
        dynamic_range="DR400",
        highlights=2,
        shadows=-1,
        color=3,
        sharpness=-2,
    )
    assert Recipe.from_dict(recipe.to_dict()) == recipe


def test_from_dict_ignores_unknown_keys():
    """from_dict ignores keys that are not recipe fields."""
    recipe = Recipe.from_dict({"film_simulation": "Acros", "bogus": 1})
    assert recipe.film_simulation == "Acros"
