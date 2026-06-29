"""Tests for the pure Recipe data model."""

from grawji.recipe import Recipe


def test_defaults():
    """A fresh recipe defaults to Provia and inherits size/quality."""
    recipe = Recipe()
    assert recipe.film_simulation == "Provia"
    assert recipe.image_size is None
    assert recipe.quality is None


def test_roundtrip_dict():
    """to_dict/from_dict round-trips a fully-populated recipe."""
    recipe = Recipe(film_simulation="Velvia", image_size="L", quality="FINE")
    assert Recipe.from_dict(recipe.to_dict()) == recipe


def test_from_dict_ignores_unknown_keys():
    """from_dict ignores keys that are not recipe fields."""
    recipe = Recipe.from_dict({"film_simulation": "Acros", "bogus": 1})
    assert recipe.film_simulation == "Acros"
