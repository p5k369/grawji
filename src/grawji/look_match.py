"""Find the camera recipe that comes closest to an edited export."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from grawji.imaging.pixbufs import area_resize

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from grawji.camera.capabilities import Capabilities
    from grawji.edit_align import Alignment
    from grawji.recipe import Recipe

# Renders a recipe of the open shot as an upright RGB array.
Render = Callable[["Recipe"], "NDArray[np.uint8]"]
# Hears about every better recipe found, and how many renders it took.
Progress = Callable[["Recipe", int], None]
# How a view is evened out against the edit before comparing.
Balance = Literal["none", "brightness", "white"]
# Hears which stage the search entered.
Stage = Callable[[str], None]

# The stages, in the order the search runs them.
STAGE_FILM = "film simulation"
STAGE_LOOK = "tones and color"
STAGE_FINE = "fine tuning"
STAGE_RECHECK = "film simulation check"

# Compare at this width: enough for the look, forgiving about detail.
COMPARE_WIDTH = 96
# A step only counts when it beats the camera's render-to-render noise.
_MARGIN = 0.15
# Passes over all controls per step size, at most.
# Could be varied depending on body as tests showed.
_SWEEPS = 4
# Step sizes from coarse to fine.
_LEVELS = 3
# How often the tuned recipe may move to another film simulation.
# todo: maybe only switching back is not enough, needs to be rechecked
_RECHECKS = 2
_STRENGTHS = ("Off", "Weak", "Strong")
_DYNAMIC_RANGES = ("DR100", "DR200", "DR400")
_MONO_SIMS = ("Acros", "AcrosR", "AcrosYe", "AcrosG")
_MONOCHROME = ("Monochrome", "MonochromeR", "MonochromeYe", "MonochromeG")
_NO_COLOR = (*_MONO_SIMS, *_MONOCHROME, "Sepia")
_EXPOSURE_RANGE = (-2.0, 3.0)
# Controls stored as floats on a half or whole step grid.
_HALF_STEP_CONTROLS = ("highlights", "shadows")
_SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_WHITE = np.array([0.95047, 1.0, 1.08883])
_SRGB_KNEE = 0.04045
_LAB_KNEE = 216 / 24389
_OPAQUE = 255


@dataclass(frozen=True)
class SearchHooks:
    """Optional ways to follow and stop a search."""

    progress: Progress | None = None
    stage: Stage | None = None
    cancelled: Callable[[], bool] | None = None


class SearchCancelledError(Exception):
    """The search was stopped before it finished."""


@dataclass(frozen=True)
class Match:
    """The outcome of a search.

    Attributes:
        recipe: The closest recipe found.
        distance: Its mean color difference to the edit (delta E).
        start_distance: The starting recipe's difference.
        renders: How many renders the camera made.
    """

    recipe: Recipe
    distance: float
    start_distance: float
    renders: int


@dataclass(frozen=True)
class _Numeric:
    """One numeric control."""

    name: str
    low: float
    high: float
    steps: tuple[float, ...]


class LookTarget:
    """An edit."""

    def __init__(
        self,
        edit: NDArray[np.uint8],
        alignment: Alignment,
        frame_size: tuple[int, int],
    ) -> None:
        """Prepare the edit.

        Args:
            edit: The edit as RGB, the size the alignment was made for.
            alignment: Where the edit sits in the frame.
            frame_size: Width and height of every render to compare.
        """
        height, width = edit.shape[:2]
        self._alignment = alignment
        self._size = (width, height)
        self._frame_size = frame_size
        small_h = max(1, round(height * COMPARE_WIDTH / width))
        self._small = (COMPARE_WIDTH, small_h)
        covered = alignment.coverage(frame_size, self._size)
        share = area_resize(
            covered.astype(np.uint8)[..., None] * _OPAQUE, *self._small
        )
        self._mask = share[..., 0] == _OPAQUE
        linear = _linear(area_resize(edit[..., :3], *self._small))
        self._lab = _lab_of_linear(linear)
        self._luminance = _mean_luminance(linear, self._mask)
        self._channels = linear[self._mask].mean(axis=0)

    @property
    def overlap(self) -> float:
        """The share of the edit the frame covers."""
        return float(self._mask.mean())

    def view(self, render: NDArray[np.uint8]) -> NDArray[np.float64]:
        """A render as the edit shows it."""
        height, width = render.shape[:2]
        if (width, height) != self._frame_size:
            msg = "render size differs from the aligned frame"
            raise ValueError(msg)
        seen = self._alignment.warp(render, self._size)
        return _linear(area_resize(seen, *self._small))

    def gain(self, view: NDArray[np.float64]) -> float:
        """The brightness factor that brings a view to the edit's."""
        own = _mean_luminance(view, self._mask)
        return self._luminance / own if own > 0 else 1.0

    def compare(
        self, view: NDArray[np.float64], *, balance: Balance = "none"
    ) -> float:
        """Mean delta E between a view and the edit."""
        if balance == "brightness":
            view = np.clip(view * self.gain(view), 0.0, 1.0)
        elif balance == "white":
            own = view[self._mask].mean(axis=0)
            gains = self._channels / np.maximum(own, 1e-9)
            view = np.clip(view * gains, 0.0, 1.0)
        delta = _lab_of_linear(view) - self._lab
        return float(np.sqrt((delta**2).sum(axis=-1))[self._mask].mean())

    def distance(self, render: NDArray[np.uint8]) -> float:
        """Mean delta E between a render and the edit."""
        return self.compare(self.view(render))


def lab(rgb: NDArray[Any]) -> NDArray[np.float64]:
    """sRGB, 0 to 255, to CIE Lab under D65."""
    return _lab_of_linear(_linear(rgb))


def _linear(rgb: NDArray[Any]) -> NDArray[np.float64]:
    """sRGB, 0 to 255, to linear light, 0 to 1."""
    value = rgb[..., :3].astype(np.float64) / 255.0
    return np.asarray(
        np.where(
            value <= _SRGB_KNEE,
            value / 12.92,
            ((value + 0.055) / 1.055) ** 2.4,
        )
    )


def _lab_of_linear(linear: NDArray[np.float64]) -> NDArray[np.float64]:
    """Linear sRGB to CIE Lab under D65."""
    xyz = linear @ _SRGB_TO_XYZ.T / _WHITE
    f = np.where(xyz > _LAB_KNEE, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack(
        [
            116 * f[..., 1] - 16,
            500 * (f[..., 0] - f[..., 1]),
            200 * (f[..., 1] - f[..., 2]),
        ],
        axis=-1,
    )


def _mean_luminance(
    linear: NDArray[np.float64], mask: NDArray[np.bool_]
) -> float:
    """Mean relative luminance over the masked pixels."""
    return float((linear @ _SRGB_TO_XYZ[1])[mask].mean())


class _Renders:
    """Renders each recipe once and keeps what the edit would show."""

    def __init__(
        self,
        render: Render,
        target: LookTarget,
        cancelled: Callable[[], bool] | None,
    ) -> None:
        self._render = render
        self._target = target
        self._cancelled = cancelled
        self._views: dict[str, NDArray[np.float64]] = {}

    @property
    def count(self) -> int:
        """How many renders the camera made."""
        return len(self._views)

    def __call__(self, recipe: Recipe) -> NDArray[np.float64]:
        key = repr(sorted(asdict(recipe).items()))
        if key not in self._views:
            if self._cancelled is not None and self._cancelled():
                raise SearchCancelledError
            self._views[key] = self._target.view(self._render(recipe))
        return self._views[key]


def search(
    start: Recipe,
    capabilities: Capabilities,
    render: Render,
    target: LookTarget,
    hooks: SearchHooks | None = None,
) -> Match:
    """The recipe whose render comes closest to the edit."""
    hooks = hooks or SearchHooks()
    progress, stage = hooks.progress, hooks.stage
    base = replace(start, grain="Off")
    renders = _Renders(render, target, hooks.cancelled)

    def look(recipe: Recipe) -> float:
        return target.compare(renders(recipe), balance="brightness")

    def film(recipe: Recipe) -> float:
        return target.compare(renders(recipe), balance="white")

    def plain(recipe: Recipe) -> float:
        return target.compare(renders(recipe))

    def better(recipe: Recipe) -> None:
        if progress is not None:
            progress(recipe, renders.count)

    def enter(name: str) -> None:
        if stage is not None:
            stage(name)

    enter(STAGE_FILM)

    start_distance = plain(base)
    # A shot's own white balance can be far from the edit's, and a color
    # simulation with the wrong cast must not lose to a neutral B&W one.
    best, best_score = base, film(base)
    for sim in capabilities.film_simulations:
        trial = _with_sim(base, sim)
        value = film(trial)
        if value < best_score:
            best, best_score = trial, value
            better(best)

    def tones_and_color(recipe: Recipe) -> list[_Numeric]:
        return [
            control
            for control in _numerics(recipe, capabilities)
            if control.name != "exposure"
        ]

    enter(STAGE_LOOK)
    best, _ = _walk(
        best,
        look,
        tones_and_color,
        lambda recipe: _choices(recipe, capabilities),
        range(_LEVELS),
        better,
    )
    enter(STAGE_FINE)
    gain = target.gain(renders(best))
    ev = best.exposure + math.log2(gain) if gain > 0 else best.exposure
    best = replace(best, exposure=_exposure_step(ev))
    better(best)

    def polish(recipe: Recipe, choices: bool) -> tuple[Recipe, float]:
        """Every control in fine steps on the plain distance, then pairs."""
        recipe, score = _walk(
            recipe,
            plain,
            lambda r: _numerics(r, capabilities),
            (lambda r: _choices(r, capabilities)) if choices else _no_choices,
            range(_LEVELS - 1, _LEVELS),
            better,
        )
        return _pair_walk(
            recipe, score, plain, _numerics(recipe, capabilities), better
        )

    best, best_score = polish(best, choices=False)
    enter(STAGE_RECHECK)
    for _round in range(_RECHECKS):
        rival, rival_score = best, best_score
        for sim in capabilities.film_simulations:
            if sim == best.film_simulation:
                continue
            trial = _with_sim(best, sim)
            value = plain(trial)
            if value < rival_score - _MARGIN:
                rival, rival_score = trial, value
        if rival is best:
            break
        better(rival)
        best, best_score = polish(rival, choices=True)
    found = replace(best, grain=start.grain, grain_size=start.grain_size)
    return Match(found, best_score, start_distance, renders.count)


def _with(recipe: Recipe, name: str, value: object) -> Recipe:
    """The recipe with one field changed."""
    changes: dict[str, Any] = {name: value}
    return replace(recipe, **changes)


def _with_sim(recipe: Recipe, sim: str) -> Recipe:
    """The recipe on another film simulation, toning reset off B&W."""
    if sim in (*_MONO_SIMS, *_MONOCHROME):
        return replace(recipe, film_simulation=sim)
    return replace(
        recipe, film_simulation=sim, mono_warm_cool=0, mono_magenta_green=0
    )


def _walk(
    best: Recipe,
    score: Callable[[Recipe], float],
    controls: Callable[[Recipe], list[_Numeric]],
    choices: Callable[[Recipe], list[tuple[str, tuple[str, ...]]]],
    levels: range,
    better: Callable[[Recipe], None],
) -> tuple[Recipe, float]:
    """Walk the controls step by step while a step helps."""
    best_score = score(best)
    for level in levels:
        for _sweep in range(_SWEEPS):
            improved = False
            for name, values in choices(best):
                for choice in values:
                    trial = _with(best, name, choice)
                    trial_score = score(trial)
                    if trial_score < best_score - _MARGIN:
                        best, best_score, improved = trial, trial_score, True
                        better(best)
            for control in controls(best):
                step = control.steps[min(level, len(control.steps) - 1)]
                moved = True
                while moved:
                    moved = False
                    for direction in (step, -step):
                        value = _snap(
                            control, getattr(best, control.name) + direction
                        )
                        if value is None:
                            continue
                        trial = _with(best, control.name, value)
                        trial_score = score(trial)
                        if trial_score < best_score - _MARGIN:
                            best, best_score = trial, trial_score
                            moved = improved = True
                            better(best)
                            break
            if not improved:
                break
    return best, best_score


def _no_choices(_recipe: Recipe) -> list[tuple[str, tuple[str, ...]]]:
    """No choice controls, for walks over numbers only."""
    return []


def _pair_walk(
    best: Recipe,
    best_score: float,
    score: Callable[[Recipe], float],
    controls: list[_Numeric],
    better: Callable[[Recipe], None],
) -> tuple[Recipe, float]:
    """Move two controls at once while that helps."""
    improved = True
    for _sweep in range(_SWEEPS):
        if not improved:
            break
        improved = False
        for first, second in itertools.combinations(controls, 2):
            for sign_a, sign_b in itertools.product((1, -1), repeat=2):
                a = _snap(
                    first,
                    getattr(best, first.name) + sign_a * first.steps[-1],
                )
                b = _snap(
                    second,
                    getattr(best, second.name) + sign_b * second.steps[-1],
                )
                if a is None or b is None:
                    continue
                trial = _with(_with(best, first.name, a), second.name, b)
                trial_score = score(trial)
                if trial_score < best_score - _MARGIN:
                    best, best_score, improved = trial, trial_score, True
                    better(best)
    return best, best_score


def _choices(
    recipe: Recipe, capabilities: Capabilities
) -> list[tuple[str, tuple[str, ...]]]:
    """The choice controls the body and the film simulation allow."""
    choices: list[tuple[str, tuple[str, ...]]] = [
        ("dynamic_range", _DYNAMIC_RANGES)
    ]
    colorful = recipe.film_simulation not in _NO_COLOR
    if capabilities.has_color_chrome and colorful:
        choices.append(("color_chrome", _STRENGTHS))
    if capabilities.has_color_chrome_blue and colorful:
        choices.append(("color_chrome_blue", _STRENGTHS))
    return choices


def _numerics(recipe: Recipe, capabilities: Capabilities) -> list[_Numeric]:
    """The numeric controls the body and the film simulation allow."""
    tone_steps = (1.0, 0.5) if capabilities.tone_half_step else (1.0,)
    low, high = capabilities.tone_min, capabilities.tone_max
    controls = [
        _Numeric("highlights", low, high, tone_steps),
        _Numeric("shadows", low, high, tone_steps),
        _Numeric("exposure", *_EXPOSURE_RANGE, (1.0, 1 / 3)),
        _Numeric("wb_shift_r", -9, 9, (4, 2, 1)),
        _Numeric("wb_shift_b", -9, 9, (4, 2, 1)),
    ]
    if recipe.film_simulation not in _NO_COLOR:
        controls.append(_Numeric("color", -4, 4, (2, 1)))
    if capabilities.has_clarity:
        controls.append(_Numeric("clarity", -5, 5, (2, 1)))
    toned = recipe.film_simulation in (*_MONO_SIMS, *_MONOCHROME)
    reach = capabilities.mono_max
    if toned and capabilities.has_mono_wc and reach:
        controls.append(_Numeric("mono_warm_cool", -reach, reach, (4, 2, 1)))
    if toned and capabilities.has_mono_mg and reach:
        controls.append(
            _Numeric("mono_magenta_green", -reach, reach, (4, 2, 1))
        )
    return controls


def _exposure_step(value: float) -> float:
    """An exposure on the camera's third-stop grid, inside its range."""
    low, high = _EXPOSURE_RANGE
    return min(high, max(low, round(value * 3) / 3))


def _snap(control: _Numeric, value: float) -> float | None:
    """A value on the control's grid, None outside its range."""
    if not control.low - 1e-9 <= value <= control.high + 1e-9:
        return None
    if control.name == "exposure":
        return round(value * 3) / 3
    if control.name in _HALF_STEP_CONTROLS:
        finest = control.steps[-1]
        return round(value / finest) * finest
    return round(value)
