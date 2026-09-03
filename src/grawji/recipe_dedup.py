"""Find duplicate saved recipes by comparing their effective look."""

from __future__ import annotations

from collections.abc import Mapping

from grawji.recipe import Recipe

# Film sims whose mono toning is applied.
_MONO_PREFIXES = ("Acros", "Monochrome")


def canonical_look(recipe: Recipe) -> tuple[object, ...]:
    """A hashable key for a recipe's effective look."""
    is_mono = recipe.film_simulation.startswith(_MONO_PREFIXES)
    is_temp = recipe.white_balance == "Temperature"
    has_grain = recipe.grain != "Off"
    return (
        recipe.film_simulation,
        recipe.white_balance,
        recipe.color_temp if is_temp else 0,
        recipe.wb_shift_r,
        recipe.wb_shift_b,
        recipe.dynamic_range,
        recipe.grain,
        recipe.grain_size if has_grain else "Small",
        recipe.color_chrome,
        recipe.color_chrome_blue,
        recipe.smooth_skin,
        recipe.highlights,
        recipe.shadows,
        recipe.color,
        recipe.sharpness,
        recipe.noise_reduction,
        recipe.clarity,
        recipe.color_space,
        recipe.mono_warm_cool if is_mono else 0,
        recipe.mono_magenta_green if is_mono else 0,
    )


def find_duplicate_recipes(
    recipes: Mapping[str, Recipe],
) -> list[list[str]]:
    """Group recipe names that share the same effective look."""
    groups: dict[tuple[object, ...], list[str]] = {}
    for name, recipe in recipes.items():
        groups.setdefault(canonical_look(recipe), []).append(name)
    return [names for names in groups.values() if len(names) > 1]


__all__ = ["canonical_look", "find_duplicate_recipes"]
