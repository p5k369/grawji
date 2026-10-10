"""How far a recipe-from-edit search is and how long it still takes."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from grawji import look_match

if TYPE_CHECKING:
    from grawji.camera.capabilities import Capabilities

# Renders the tuning typically takes per control the search moves.
# (from test runs on the X100F, X-T3 and X-E5).
_RENDERS_PER_CONTROL = 30
# The controls every body has: tones, exposure, both shifts, color, white
# balance and dynamic range.
_BASE_CONTROLS = 8
# Half tone steps cost about one more control each for highlights and
# shadows.
_HALF_STEP_CONTROLS = 2
# What the final check adds to one render per film simulation, and what
# trying clarity on the finalists takes.
_RECHECK_POLISH = 10
_CLARITY_RENDERS = 10
# Renders still ahead at the least, before and within the final check.
_TUNING_FLOOR = 60
_RECHECK_FLOOR = 10
# The second film simulation takes a little longer than the first, the
# final moves of two controls at once come on top of it.
_SECOND_CANDIDATE = 1.2
# Renders measured before a time is guessed, and the recent renders of
# each kind the pace is taken from.
_MEASURED = 12
_PACE_WINDOW = 25
# A guess above the shown one replaces it only when it is this much
# longer, so the time does not flicker up and down.
_RISE = 1.25
_MINUTE = 60


class SearchEstimate:
    """Counts the camera's renders and guesses what is left."""

    def __init__(self, capabilities: Capabilities, now: float) -> None:
        """Start counting for a body at this monotonic time."""
        self._sims = len(capabilities.film_simulations)
        self._last_stage = self._sims + _RECHECK_POLISH
        if capabilities.has_clarity:
            self._last_stage += _CLARITY_RENDERS
        self._expected = (
            self._sims
            + 1
            + _RENDERS_PER_CONTROL * _controls(capabilities)
            + self._last_stage
        )
        self._looks = 0
        self._tuning_began = 0
        self._recheck = False
        self._done = 0
        self._last = now
        self._plain: deque[float] = deque(maxlen=_PACE_WINDOW)
        self._clarity: deque[float] = deque(maxlen=_PACE_WINDOW)
        self._fraction = 0.0
        self._seconds: float | None = None

    def enter(self, stage: str) -> None:
        """Note a new stage and correct the guess with what it tells."""
        if stage == look_match.STAGE_LOOK:
            self._looks += 1
            if self._looks == 1:
                self._tuning_began = self._done
            else:
                first = self._done - self._tuning_began
                self._expected = (
                    self._done
                    + round(_SECOND_CANDIDATE * first)
                    + self._last_stage
                )
        elif stage == look_match.STAGE_RECHECK and not self._recheck:
            self._recheck = True
            self._expected = self._done + self._last_stage

    def rendered(self, now: float, *, clarity: bool = False) -> None:
        """Note one render finished at this monotonic time."""
        self._done += 1
        (self._clarity if clarity else self._plain).append(now - self._last)
        self._last = now
        remaining = self._remaining()
        self._fraction = max(
            self._fraction, self._done / (self._done + remaining)
        )
        if self._done < _MEASURED:
            return
        timed = self._clarity or self._plain
        guess = sum(timed) / len(timed) * remaining
        shown = self._seconds
        if shown is None or guess < shown or guess > shown * _RISE:
            self._seconds = guess

    @property
    def fraction(self) -> float:
        """The share of the search done."""
        return self._fraction

    @property
    def seconds_left(self) -> float | None:
        """The time still ahead."""
        return self._seconds

    def _remaining(self) -> int:
        """The renders still ahead."""
        floor = _RECHECK_FLOOR if self._recheck else self._sims + _TUNING_FLOOR
        return max(self._expected - self._done, floor)


def _controls(capabilities: Capabilities) -> int:
    """How many controls the search moves on this body, roughly."""
    return (
        _BASE_CONTROLS
        + capabilities.has_color_chrome
        + capabilities.has_color_chrome_blue
        + _HALF_STEP_CONTROLS * capabilities.tone_half_step
    )


def describe(seconds: float | None) -> str:
    """The time left in rough words."""
    if seconds is None:
        return ""
    if seconds < _MINUTE:
        return "less than a minute left"
    minutes = round(seconds / _MINUTE)
    if minutes == 1:
        return "about 1 minute left"
    return f"about {minutes} minutes left"
