"""Tests for the progress and time guess of a recipe-from-edit search."""

import pytest

from grawji import look_match
from grawji.camera.capabilities import Capabilities
from grawji.search_estimate import SearchEstimate, describe

CAPS = Capabilities(film_simulations=tuple(f"sim{n}" for n in range(10)))


def test_no_time_is_guessed_before_enough_renders() -> None:
    """The first renders only measure."""
    estimate = SearchEstimate(CAPS, 0.0)
    for count in range(1, 6):
        estimate.rendered(count * 0.5)
    assert estimate.seconds_left is None
    assert 0 < estimate.fraction < 0.1


def test_the_time_left_follows_the_measured_pace() -> None:
    """Half a second per render with many renders ahead is minutes."""
    estimate = SearchEstimate(CAPS, 0.0)
    for count in range(1, 21):
        estimate.rendered(count * 0.5)
    seconds = estimate.seconds_left
    assert seconds is not None
    assert 100 < seconds < 200


def test_the_share_never_shrinks() -> None:
    """A slower stretch or a new stage does not move the bar back."""
    estimate = SearchEstimate(CAPS, 0.0)
    now, fraction = 0.0, 0.0
    for count in range(400):
        now += 0.2 if count < 100 else 2.0
        if count == 300:
            estimate.enter(look_match.STAGE_RECHECK)
        estimate.rendered(now)
        assert estimate.fraction >= fraction
        fraction = estimate.fraction
    assert fraction < 1


def test_a_timed_clarity_render_sets_the_pace() -> None:
    """Once a slow clarity render was timed, the rest is guessed slow."""
    estimate = SearchEstimate(CAPS, 0.0)
    for count in range(1, 21):
        estimate.rendered(count * 0.5)
    fast = estimate.seconds_left
    estimate.rendered(11.5, clarity=True)
    slow = estimate.seconds_left
    assert fast is not None
    assert slow is not None
    assert slow > 2 * fast


def test_a_small_rise_does_not_change_the_shown_time() -> None:
    """The time only goes up again for a clearly longer guess."""
    estimate = SearchEstimate(CAPS, 0.0)
    for count in range(1, 21):
        estimate.rendered(count * 0.5)
    shown = estimate.seconds_left
    estimate.rendered(10.6)
    assert estimate.seconds_left == shown


def test_the_final_check_brings_the_end_near() -> None:
    """Entering the final check leaves about one render per simulation."""
    estimate = SearchEstimate(CAPS, 0.0)
    for count in range(1, 101):
        estimate.rendered(count * 0.5)
    early = estimate.fraction
    estimate.enter(look_match.STAGE_RECHECK)
    estimate.rendered(50.5)
    assert estimate.fraction > early + 0.2


def test_the_time_reads_in_rough_minutes() -> None:
    """Whole minutes, and no number below one."""
    assert describe(None) == ""
    assert describe(40) == "less than a minute left"
    assert describe(80) == "about 1 minute left"
    assert describe(250) == "about 4 minutes left"


def test_a_third_candidate_lengthens_the_guess() -> None:
    """Told of three simulations to tune, more renders lie ahead."""
    two = SearchEstimate(CAPS, 0.0)
    three = SearchEstimate(CAPS, 0.0)
    three.plan(3)
    for count in range(1, 21):
        two.rendered(count * 0.5)
        three.rendered(count * 0.5)
    assert two.seconds_left is not None
    assert three.seconds_left is not None
    assert three.seconds_left > 1.3 * two.seconds_left
    assert three.fraction < two.fraction


def test_the_texture_stage_is_planned_and_paced_apart() -> None:
    """A texture search is counted from the start and timed on its own."""
    plain = SearchEstimate(CAPS, 0.0)
    textured = SearchEstimate(CAPS, 0.0, texture=True)
    for count in range(1, 21):
        plain.rendered(count * 0.5)
        textured.rendered(count * 0.5)
    assert textured.fraction < plain.fraction
    plain_left = plain.seconds_left
    textured_left = textured.seconds_left
    assert plain_left is not None and textured_left is not None
    assert textured_left > plain_left
    now = 10.0
    textured.enter(look_match.STAGE_TEXTURE)
    before = textured.fraction
    for _ in range(5):
        now += 2.0
        textured.rendered(now, texture=True)
    assert textured.fraction > before
    left = textured.seconds_left
    assert left is not None
    # 20 planned, 5 done, at the measured two seconds each.
    assert 20 < left < 40


def test_clarity_does_not_slow_the_texture_guess() -> None:
    """A timed clarity render leaves the full-size guess at thumbnail pace."""
    plain = SearchEstimate(CAPS, 0.0)
    textured = SearchEstimate(CAPS, 0.0, texture=True)
    for count in range(1, 21):
        plain.rendered(count * 0.5)
        textured.rendered(count * 0.5)
    plain.rendered(11.5, clarity=True)
    textured.rendered(11.5, clarity=True)
    assert plain.seconds_left is not None
    assert textured.seconds_left is not None
    # The 20 planned full-size renders add 4.5 thumbnail paces each,
    # not 4.5 clarity paces.
    extra = textured.seconds_left - plain.seconds_left
    assert extra == pytest.approx(20 * 0.5 * 4.5, abs=5)
