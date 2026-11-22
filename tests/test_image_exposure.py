"""Tests for tracking the open image's exposure."""

from __future__ import annotations

from grawji.image_exposure import ImageExposure


def _opened(*, shot: float = 0.0, current: float = 0.0) -> ImageExposure:
    """An image that has opened with the given exposures."""
    exposure = ImageExposure()
    exposure.begin(None)
    exposure.opened(shot, current)
    return exposure


def test_an_unchanged_exposure_stores_nothing() -> None:
    """Settling on the value the image opened with is not a change."""
    assert _opened(current=0.33).settle(0.33) == (False, None)


def test_a_changed_exposure_is_stored() -> None:
    """A new value becomes the image's override."""
    exposure = _opened()
    assert exposure.settle(1.0) == (True, 1.0)
    assert exposure.settle(1.0) == (False, None)


def test_going_back_to_the_shot_exposure_clears_the_override() -> None:
    """The shot's own value needs no entry beside the image."""
    exposure = _opened(shot=0.33, current=1.0)
    assert exposure.settle(0.33) == (True, None)


def test_a_third_of_a_stop_survives_rounding() -> None:
    """A value a rounding error away from the shot counts as the shot."""
    exposure = _opened(shot=1 / 3, current=1.0)
    assert exposure.settle(0.3333333333) == (True, None)


def test_before_the_image_opened_any_value_is_stored() -> None:
    """Without a known shot exposure nothing can be told apart from it."""
    exposure = ImageExposure()
    exposure.begin(0.67)
    assert exposure.stored == 0.67
    assert exposure.settle(0.0) == (True, 0.0)


def test_a_new_image_forgets_the_last_one() -> None:
    """Beginning an image clears what the previous one held."""
    exposure = _opened(shot=0.33, current=1.0)
    exposure.begin(None)
    assert (exposure.stored, exposure.current, exposure.shot) == (
        None,
        None,
        None,
    )
