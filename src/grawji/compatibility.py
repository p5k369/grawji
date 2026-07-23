"""Judge how well a recipe fits a given camera body."""

from __future__ import annotations

from dataclasses import dataclass

from grawji.capabilities import Capabilities
from grawji.recipe import Recipe

# Verdict levels, worst last.
FULL = "full"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"

_BW_PREFIXES = ("Acros", "Monochrome")


@dataclass(frozen=True)
class Compatibility:
    """How a recipe fares on one body."""

    level: str
    issues: list[str]

    @property
    def is_full(self) -> bool:
        """Whether the recipe renders exactly as intended."""
        return self.level == FULL


def evaluate(recipe: Recipe, caps: Capabilities) -> Compatibility:
    """Return how recipe fits a body with the given capabilities."""
    issues: list[str] = []

    sim_missing = recipe.film_simulation not in caps.film_simulations
    if sim_missing:
        issues.append(
            f"film simulation {recipe.film_simulation} is not on this body"
        )

    if recipe.color_chrome != "Off" and not caps.has_color_chrome:
        issues.append("Color Chrome Effect dropped")
    if recipe.color_chrome_blue != "Off" and not caps.has_color_chrome_blue:
        issues.append("Color Chrome FX Blue dropped")
    if recipe.clarity and not caps.has_clarity:
        issues.append("Clarity dropped")
    if recipe.smooth_skin != "Off" and not caps.has_smooth_skin:
        issues.append("Smooth Skin dropped")
    if recipe.grain_size == "Large" and not caps.has_grain_size:
        issues.append("grain size reduced to Small")

    is_bw = recipe.film_simulation.startswith(_BW_PREFIXES)
    if is_bw and recipe.mono_warm_cool and not caps.has_mono_wc:
        issues.append("monochromatic warm/cool toning dropped")
    if is_bw and recipe.mono_magenta_green and not caps.has_mono_mg:
        issues.append("monochromatic magenta/green toning dropped")

    half_step = recipe.highlights % 1 or recipe.shadows % 1
    if half_step and not caps.tone_half_step:
        issues.append("half-step tones rounded to whole steps")
    for label, value in (
        ("highlight", recipe.highlights),
        ("shadow", recipe.shadows),
    ):
        if not caps.tone_min <= value <= caps.tone_max:
            issues.append(
                f"{label} tone clamped to "
                f"{caps.tone_min:+g}..{caps.tone_max:+g}"
            )

    if sim_missing:
        level = UNAVAILABLE
    elif issues:
        level = DEGRADED
    else:
        level = FULL
    return Compatibility(level=level, issues=issues)
