"""Tests for recipe-vs-body compatibility verdicts."""

from grawji.capabilities import capabilities_for_model
from grawji.compatibility import DEGRADED, FULL, UNAVAILABLE, evaluate
from grawji.recipe import Recipe

_X100F = capabilities_for_model("X100F")  # gen3
_XT3 = capabilities_for_model("X-T3")  # gen4-early
_XE5 = capabilities_for_model("X-E5")  # gen5


def test_portable_recipe_is_full_everywhere():
    """A recipe using only universal features is FULL on every body."""
    recipe = Recipe(film_simulation="ProNegStd", color=4, noise_reduction=-3)
    for caps in (_X100F, _XT3, _XE5):
        assert evaluate(recipe, caps).level == FULL


def test_missing_film_sim_is_unavailable():
    """A film sim the body lacks breaks the look, so it is UNAVAILABLE."""
    recipe = Recipe(film_simulation="NostalgicNeg")  # gen5 only
    assert evaluate(recipe, _XE5).level == FULL
    assert evaluate(recipe, _XT3).level == UNAVAILABLE
    assert evaluate(recipe, _X100F).level == UNAVAILABLE


def test_degraded_lists_dropped_effects():
    """Missing effects degrade the verdict and each one is named."""
    # ClassicChrome is on all three, but Color Chrome is gen4+, FX Blue gen5.
    recipe = Recipe(
        film_simulation="ClassicChrome",
        color_chrome="Strong",
        color_chrome_blue="Weak",
    )
    x100f = evaluate(recipe, _X100F)
    assert x100f.level == DEGRADED
    assert any("Color Chrome Effect" in i for i in x100f.issues)
    assert any("FX Blue" in i for i in x100f.issues)
    # On the X-T3 only FX Blue is missing.
    xt3 = evaluate(recipe, _XT3)
    assert xt3.level == DEGRADED
    assert any("FX Blue" in i for i in xt3.issues)
    assert not any("Color Chrome Effect" in i for i in xt3.issues)
    assert evaluate(recipe, _XE5).is_full


def test_half_step_tone_degrades_on_non_xprocessor5():
    """Half-step tones are rounded on older bodies, a DEGRADED note."""
    recipe = Recipe(film_simulation="Provia", highlights=-0.5)
    assert evaluate(recipe, _XE5).is_full  # half steps supported
    verdict = evaluate(recipe, _XT3)
    assert verdict.level == DEGRADED
    assert any("half-step" in i for i in verdict.issues)


def test_mono_toning_only_counts_for_bw_sims():
    """Mono toning fields matter only when the sim is black and white."""
    recipe = Recipe(film_simulation="Provia", mono_magenta_green=10)
    assert evaluate(recipe, _XT3).is_full
    # On a B&W sim the X-T3 (no MG axis) flags it.
    bw = Recipe(film_simulation="Acros", mono_magenta_green=10)
    assert evaluate(bw, _XT3).level == DEGRADED
