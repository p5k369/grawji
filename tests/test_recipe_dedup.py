"""Tests for duplicate saved-recipe detection."""

from __future__ import annotations

from grawji.recipe import Recipe
from grawji.recipe_dedup import canonical_look, find_duplicate_recipes


def test_identical_recipes_group_together():
    """Two recipes with the same look are one duplicate group."""
    recipes = {
        "Punchy": Recipe(film_simulation="Velvia", color=2),
        "Vivid": Recipe(film_simulation="Velvia", color=2),
        "Soft": Recipe(film_simulation="Astia"),
    }
    assert find_duplicate_recipes(recipes) == [["Punchy", "Vivid"]]


def test_exposure_and_origin_are_ignored():
    """Per-image EV and provenance do not distinguish a look."""
    recipes = {
        "A": Recipe(
            film_simulation="Provia", exposure=1.0, origin_body="X-T3"
        ),
        "B": Recipe(
            film_simulation="Provia", exposure=-2.0, origin_body="X-E5"
        ),
    }
    assert find_duplicate_recipes(recipes) == [["A", "B"]]


def test_dormant_fields_do_not_split_a_duplicate():
    """A stray value on an inactive control still reads as the same look."""
    temp = {
        "A": Recipe(white_balance="Daylight", color_temp=5000),
        "B": Recipe(white_balance="Daylight", color_temp=8000),
    }
    assert find_duplicate_recipes(temp) == [["A", "B"]]
    grain = {
        "A": Recipe(grain="Off", grain_size="Small"),
        "B": Recipe(grain="Off", grain_size="Large"),
    }
    assert find_duplicate_recipes(grain) == [["A", "B"]]


def test_active_fields_do_split():
    """When the differing field is actually applied, they are distinct."""
    temp = {
        "A": Recipe(white_balance="Temperature", color_temp=5000),
        "B": Recipe(white_balance="Temperature", color_temp=8000),
    }
    assert find_duplicate_recipes(temp) == []


def test_mono_toning_matters_only_for_bw_sims():
    """Mono toning splits B&W sims but is ignored on color sims."""
    color = {
        "A": Recipe(film_simulation="Velvia", mono_warm_cool=5),
        "B": Recipe(film_simulation="Velvia", mono_warm_cool=-5),
    }
    assert find_duplicate_recipes(color) == [["A", "B"]]
    mono = {
        "A": Recipe(film_simulation="Acros", mono_warm_cool=5),
        "B": Recipe(film_simulation="Acros", mono_warm_cool=-5),
    }
    assert find_duplicate_recipes(mono) == []


def test_no_duplicates_returns_empty():
    """Distinct recipes produce no groups."""
    recipes = {
        "A": Recipe(film_simulation="Velvia"),
        "B": Recipe(film_simulation="Acros"),
    }
    assert find_duplicate_recipes(recipes) == []


def test_three_way_and_order_preserved():
    """A group keeps input order."""
    recipes = {
        "a1": Recipe(film_simulation="Provia"),
        "b1": Recipe(film_simulation="Velvia"),
        "a2": Recipe(film_simulation="Provia"),
        "b2": Recipe(film_simulation="Velvia"),
        "a3": Recipe(film_simulation="Provia"),
    }
    assert find_duplicate_recipes(recipes) == [
        ["a1", "a2", "a3"],
        ["b1", "b2"],
    ]


def test_canonical_look_is_hashable_and_stable():
    """The key is hashable and equal for equal looks."""
    a = canonical_look(Recipe(film_simulation="Velvia", color=1))
    b = canonical_look(Recipe(film_simulation="Velvia", color=1, exposure=2.0))
    assert a == b
    assert len({a, b}) == 1
