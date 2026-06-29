"""Tests for the pure Recipe data model."""

from grawji.recipe import Recipe


def test_defaults():
    recipe = Recipe()
    assert recipe.film_simulation == "Provia"
    assert recipe.image_size is None
    assert recipe.quality is None


def test_roundtrip_dict():
    recipe = Recipe(film_simulation="Velvia", image_size="L", quality="FINE")
    assert Recipe.from_dict(recipe.to_dict()) == recipe


def test_from_dict_ignores_unknown_keys():
    recipe = Recipe.from_dict({"film_simulation": "Acros", "bogus": 1})
    assert recipe.film_simulation == "Acros"
