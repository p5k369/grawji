"""Thin adapter around the rawji library.

Implements the "load once, render many" workflow and grawji's own
read-modify-write (RMW) profile strategy:

    open RAF (once, slow):  connect -> send_raf -> get_profile
    change recipe (often):  apply_recipe(base, recipe) -> set_profile
                            -> trigger_conversion -> wait_for_result
    quit:                   disconnect

Order matters: send_raf *before* get_profile; profile-set
*before* trigger. send_raf runs only on open, never per slider move.

"""

from __future__ import annotations

import contextlib
import struct
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import rawji
from rawji.fuji_enums import (
    GrainEffect,
    ev_to_int,
    int_to_ev,
    validate_color_temp,
    validate_wb_shift,
)
from rawji.fuji_profile import (
    INDEX_TO_PARAM,
    PROFILE_PARAMS_OFFSET,
    TONE_PARAMS,
    decode_tone_value,
    encode_tone_value,
)

from grawji.recipe import Recipe

OFFSET_FILM_SIM = 541

# rawji parameter name -> byte offset in the native profile, derived from
# rawji's layout (PROFILE_PARAMS_OFFSET + index * 4).
_index_items = (
    INDEX_TO_PARAM.items()
    if isinstance(INDEX_TO_PARAM, dict)
    else enumerate(INDEX_TO_PARAM)
)
_PARAM_OFFSETS = {
    name: PROFILE_PARAMS_OFFSET + index * 4 for index, name in _index_items
}

# Default wait_for_result timeout, in seconds.
DEFAULT_TIMEOUT = 30


class CameraError(RuntimeError):
    """A camera / PTP operation failed."""


class ForeignRafError(CameraError):
    """The RAF was shot by a different body (PTP error 0x2002).

    Fuji cameras only convert their own RAFs; sending a foreign file
    makes get_profile fail with 0x2002.
    """


class SessionStateError(CameraError):
    """A render was requested before a RAF was opened."""


def rmw_patch(base: bytes, film_sim_byte: int) -> bytes:
    """Read-modify-write a native camera profile in place.

    Patches only the verified bytes, leaving the RAF's own recipe
    intact. This is intentionally a small, dependency-free helper so it
    can be unit-tested without a camera.

    Args:
        base: The profile bytes read from the camera via get_profile.
        film_sim_byte: The film-simulation byte to write at
            OFFSET_FILM_SIM.

    Returns:
        A new bytes object with the patched profile.

    Raises:
        ValueError: If base is too short to hold the offset.
    """
    if len(base) <= OFFSET_FILM_SIM:
        msg = (
            f"profile too short ({len(base)} bytes) to patch offset "
            f"{OFFSET_FILM_SIM}"
        )
        raise ValueError(msg)
    out = bytearray(base)
    out[OFFSET_FILM_SIM] = film_sim_byte
    return bytes(out)


def _enum_value(enum_cls: Any, name: str, kind: str) -> int:
    """Return the integer profile value for an enum member name.

    Uses exact member lookup (enum_cls[name]) rather than rawji's
    from_name, which mangles camelCase names like "AsShot".

    Args:
        enum_cls: A rawji IntEnum (FilmSimulation / WhiteBalance / ...).
        name: The exact enum member name (as in e.name).
        kind: Human-readable kind, for error messages.

    Raises:
        ValueError: If name is not a member of enum_cls.
    """
    try:
        return int(enum_cls[name])
    except KeyError as e:
        msg = f"unknown {kind}: {name}"
        raise ValueError(msg) from e


def film_simulation_byte(name: str) -> int:
    """Return the profile byte for a film-simulation member name.

    Args:
        name: Film-simulation member name, e.g. "Velvia".

    Returns:
        The byte value written at OFFSET_FILM_SIM.

    Raises:
        ValueError: If the name is not a known film simulation.
    """
    return _enum_value(rawji.FilmSimulation, name, "film simulation")


def recipe_changes(recipe: Recipe) -> dict[str, int]:
    """Map a recipe to rawji profile parameter values (validated).

    Args:
        recipe: The recipe to translate.

    Returns:
        A dict of rawji parameter name -> integer value. Tone values are
        the raw user values here; encoding happens in apply_recipe().

    Raises:
        ValueError: If a name is unknown or a tone value is out of range.
    """
    film_sim = _enum_value(
        rawji.FilmSimulation, recipe.film_simulation, "film simulation"
    )
    rawji.validate_params(
        film_sim=film_sim,
        highlights=recipe.highlights,
        shadows=recipe.shadows,
        color=recipe.color,
        sharpness=recipe.sharpness,
    )
    changes = {
        "FilmSimulation": film_sim,
        "ExposureBias": ev_to_int(recipe.exposure),
        "DynamicRange": _enum_value(
            rawji.DynamicRange, recipe.dynamic_range, "dynamic range"
        ),
        "GrainEffect": _enum_value(GrainEffect, recipe.grain, "grain effect"),
        "HighlightTone": recipe.highlights,
        "ShadowTone": recipe.shadows,
        "Color": recipe.color,
        "Sharpness": recipe.sharpness,
        "WBShiftR": validate_wb_shift(recipe.wb_shift_r),
        "WBShiftB": validate_wb_shift(recipe.wb_shift_b),
    }
    # "AsShot" leaves the RAF's own white balance untouched.
    if recipe.white_balance != "AsShot":
        changes["WBShootCond"] = 2
        changes["WhiteBalance"] = _enum_value(
            rawji.WhiteBalance, recipe.white_balance, "white balance"
        )
        # Colour temperature only takes effect in Temperature mode.
        if recipe.white_balance == "Temperature":
            changes["WBColorTemp"] = validate_color_temp(recipe.color_temp)
    return changes


def apply_recipe(base: bytes, recipe: Recipe) -> bytes:
    """Apply a recipe to a native camera profile (read-modify-write).

    Patches only the recipe's parameters in place using rawji's profile
    layout, leaving the RAF's own values for everything else intact.

    Args:
        base: Profile bytes read from the camera.
        recipe: The recipe to apply.

    Returns:
        A new profile with the recipe's parameters patched in.

    Raises:
        ValueError: If a name is unknown, a value is out of range, or the
            profile is too short to hold a parameter's offset.
    """
    out = bytearray(base)
    for name, value in recipe_changes(recipe).items():
        offset = _PARAM_OFFSETS[name]
        if offset + 4 > len(out):
            msg = f"profile too short ({len(base)} bytes) for {name}"
            raise ValueError(msg)
        encoded = encode_tone_value(value) if name in TONE_PARAMS else value
        if encoded < 0:
            encoded = (1 << 32) + encoded
        struct.pack_into("<I", out, offset, encoded)
    return bytes(out)


def _enum_name(enum_cls: Any, value: int, fallback: str) -> str:
    """Return the member name for an enum value, or fallback."""
    try:
        return str(enum_cls(value).name)
    except ValueError:
        return fallback


def recipe_from_profile(base: bytes) -> Recipe:
    """Decode a recipe from a native camera profile (inverse of apply).

    Reads the recipe parameters back out of the profile the camera
    reported, so the UI can start from the image's own in-camera settings.
    Values that don't fit (unknown enum value, too-short profile) fall
    back to the Recipe defaults.

    Args:
        base: Profile bytes read from the camera.

    Returns:
        The recipe encoded in the profile.
    """
    defaults = Recipe()

    def signed(name: str, fallback: int = 0) -> int:
        offset = _PARAM_OFFSETS[name]
        if offset + 4 > len(base):
            return fallback
        return int(struct.unpack("<i", base[offset : offset + 4])[0])

    color_temp = signed("WBColorTemp", defaults.color_temp)
    return Recipe(
        film_simulation=_enum_name(
            rawji.FilmSimulation,
            signed("FilmSimulation", 1),
            defaults.film_simulation,
        ),
        white_balance=_enum_name(
            rawji.WhiteBalance, signed("WhiteBalance"), defaults.white_balance
        ),
        dynamic_range=_enum_name(
            rawji.DynamicRange,
            signed("DynamicRange", 1),
            defaults.dynamic_range,
        ),
        grain=_enum_name(
            GrainEffect, signed("GrainEffect", 1), defaults.grain
        ),
        exposure=int_to_ev(signed("ExposureBias")),
        highlights=decode_tone_value(signed("HighlightTone")),
        shadows=decode_tone_value(signed("ShadowTone")),
        color=decode_tone_value(signed("Color")),
        sharpness=decode_tone_value(signed("Sharpness")),
        wb_shift_r=signed("WBShiftR"),
        wb_shift_b=signed("WBShiftB"),
        color_temp=color_temp if color_temp > 0 else defaults.color_temp,
    )


class CameraSession:
    """A "load once, render many" session around rawji.FujiCamera.

    Opening a RAF is slow (seconds, a multi-MB USB upload) and is done
    once; the session then stays open so recipes can be applied and
    rendered repeatedly without re-uploading the RAF. All calls are
    serialised (one camera op at a time) via an internal lock.
    """

    def __init__(
        self,
        *,
        camera_factory: Callable[[], Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Create a session.

        Args:
            camera_factory: Callable returning a fresh camera object.
                Defaults to rawji.FujiCamera. Injectable for tests.
            timeout: wait_for_result timeout in seconds.
        """
        self._camera_factory: Callable[[], Any] = (
            camera_factory or rawji.FujiCamera
        )
        self._timeout = timeout
        self._lock = threading.Lock()
        self._camera: Any | None = None
        self._base_profile: bytes | None = None
        self._raf_path: Path | None = None

    @property
    def is_open(self) -> bool:
        """Whether a RAF is currently open."""
        return self._camera is not None

    @property
    def raf_path(self) -> Path | None:
        """Path of the currently open RAF, if any."""
        return self._raf_path

    @property
    def profile(self) -> bytes | None:
        """The native profile read from the camera on open, if any."""
        return self._base_profile

    def open(self, raf_path: str | Path) -> None:
        """Connect and load a RAF (slow; call once per image).

        Order matters: send_raf must precede get_profile so the
        camera reports a valid profile. Any previously open session is
        closed first.

        Args:
            raf_path: Path to the RAF file. Must be from the connected
                body, or the camera fails with 0x2002.

        Raises:
            CameraError: If the camera cannot be connected.
            ForeignRafError: If the RAF was shot by a different body.
        """
        with self._lock:
            self._close_locked()
            camera = self._camera_factory()
            try:
                if not camera.connect():
                    raise CameraError("could not connect to camera")
                camera.send_raf(str(raf_path))
                base: bytes = camera.get_profile()
            except Exception as e:
                self._safe_disconnect(camera)
                if "0x2002" in str(e):
                    raise ForeignRafError(
                        "RAF was shot by a different camera body (PTP 0x2002)"
                    ) from e
                raise
            self._camera = camera
            self._base_profile = base
            self._raf_path = Path(raf_path)

    def render(self, recipe: Recipe, *, full_resolution: bool) -> bytes:
        """Apply a recipe and render the open RAF (fast; call often).

        Does NOT re-send the RAF - the session and uploaded RAF stay
        open, which is what keeps the live preview responsive.

        Args:
            recipe: The recipe to apply.
            full_resolution: False for a fast preview (ignores
                profile size), True for a full-resolution export.

        Returns:
            The rendered JPEG bytes.

        Raises:
            SessionStateError: If no RAF is open.
        """
        with self._lock:
            if self._camera is None or self._base_profile is None:
                raise SessionStateError("no RAF open; call open() first")
            profile = apply_recipe(self._base_profile, recipe)
            self._camera.set_profile(profile)
            self._camera.trigger_conversion(full_resolution=full_resolution)
            return cast("bytes", self._camera.wait_for_result(self._timeout))

    def close(self) -> None:
        """Disconnect and reset the session (idempotent)."""
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        """Disconnect and reset session state (caller holds the lock)."""
        if self._camera is not None:
            self._safe_disconnect(self._camera)
        self._camera = None
        self._base_profile = None
        self._raf_path = None

    @staticmethod
    def _safe_disconnect(camera: Any) -> None:
        """Disconnect a camera, ignoring any teardown errors."""
        with contextlib.suppress(Exception):
            camera.disconnect()

    def __enter__(self) -> CameraSession:
        """Enter the runtime context, returning the session."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the session on context exit."""
        self.close()
