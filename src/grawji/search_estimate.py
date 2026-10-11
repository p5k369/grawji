"""How far a recipe-from-edit search is and how long it still takes."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from grawji import look_match

if TYPE_CHECKING:
    from grawji.camera.capabilities import Capabilities

# Renders tuning one film simulation typically takes per control the
# search moves.
_RENDERS_PER_CONTROL = 14
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
# What the texture stage takes, and how much slower its full-size
# renders are than the thumbnails until one was timed.
_TEXTURE_RENDERS = 30
_TEXTURE_SLOWER = 3.0
_TEXTURE_FLOOR = 4
# Renders still ahead at the least, before and within the final check.
_TUNING_FLOOR = 60
_RECHECK_FLOOR = 10
# The final moves of two controls at once, after the last simulation,
# take about this share of tuning one.
_PAIRS_SHARE = 0.2
# Simulations tuned until the search says how many.
_DEFAULT_CANDIDATES = 2
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

    def __init__(
        self, capabilities: Capabilities, now: float, *, texture: bool = False
    ) -> None:
        """Start counting for a body at this monotonic time.

        Args:
            capabilities: What the body offers.
            now: The time the search started.
            texture: Whether the search ends with the texture stage.
        """
        self._sims = len(capabilities.film_simulations)
        self._last_stage = self._sims + _RECHECK_POLISH
        if capabilities.has_clarity:
            self._last_stage += _CLARITY_RENDERS
        self._texture_ahead = _TEXTURE_RENDERS if texture else 0
        self._last_stage += self._texture_ahead
        self._in_texture = False
        self._per_candidate = float(
            _RENDERS_PER_CONTROL * _controls(capabilities)
        )
        self._candidates = _DEFAULT_CANDIDATES
        self._expected = self._sims + 1 + self._tuning_left(0)
        self._looks = 0
        self._tuning_began = 0
        self._recheck = False
        self._done = 0
        self._last = now
        self._plain: deque[float] = deque(maxlen=_PACE_WINDOW)
        self._clarity: deque[float] = deque(maxlen=_PACE_WINDOW)
        self._texture: deque[float] = deque(maxlen=_PACE_WINDOW)
        self._fraction = 0.0
        self._seconds: float | None = None

    def enter(self, stage: str) -> None:
        """Note a new stage and correct the guess with what it tells."""
        if stage == look_match.STAGE_LOOK:
            self._looks += 1
            if self._looks == 1:
                self._tuning_began = self._done
            else:
                tuned = self._looks - 1
                self._per_candidate = (self._done - self._tuning_began) / tuned
                self._expected = self._done + self._tuning_left(tuned)
        elif stage == look_match.STAGE_RECHECK and not self._recheck:
            self._recheck = True
            self._expected = self._done + self._last_stage
        elif stage == look_match.STAGE_TEXTURE and not self._in_texture:
            self._in_texture = True
            self._expected = self._done + self._texture_ahead

    def plan(self, candidates: int) -> None:
        """Note how many film simulations the search will tune."""
        self._candidates = candidates
        tuned = max(0, self._looks - 1)
        self._expected = self._done + self._tuning_left(tuned)

    def _tuning_left(self, tuned: int) -> int:
        """The renders still ahead once this many simulations are tuned."""
        left = max(0, self._candidates - tuned) + _PAIRS_SHARE
        return round(self._per_candidate * left) + self._last_stage

    def rendered(
        self, now: float, *, clarity: bool = False, texture: bool = False
    ) -> None:
        """Note one render finished at this time.

        Args:
            now: The time it finished.
            clarity: Whether it was a slow clarity render.
            texture: Whether it was a full-size render of the texture stage.
        """
        self._done += 1
        kind = (
            self._texture
            if texture
            else (self._clarity if clarity else self._plain)
        )
        kind.append(now - self._last)
        self._last = now
        remaining = self._remaining()
        self._fraction = max(
            self._fraction, self._done / (self._done + remaining)
        )
        if self._done < _MEASURED:
            return
        guess = self._guess(remaining)
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

    def _guess(self, remaining: int) -> float:
        """The seconds the remaining renders take at the measured pace."""
        plain = sum(self._plain) / len(self._plain) if self._plain else 0.0
        timed = self._clarity or self._plain
        pace = sum(timed) / len(timed) if timed else 0.0
        if self._texture:
            texture_pace = sum(self._texture) / len(self._texture)
        else:
            texture_pace = plain * _TEXTURE_SLOWER
        if self._in_texture:
            return texture_pace * remaining
        ahead = min(self._texture_ahead, remaining)
        return pace * (remaining - ahead) + texture_pace * ahead

    def _remaining(self) -> int:
        """The renders still ahead."""
        if self._in_texture:
            floor = _TEXTURE_FLOOR
        elif self._recheck:
            floor = _RECHECK_FLOOR
        else:
            floor = self._sims + _TUNING_FLOOR
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
