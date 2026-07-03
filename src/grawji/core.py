"""Thin adapter around the rawji library.

Implements the "load once, render many" workflow and grawji's own
read-modify-write (RMW) profile strategy:

    open RAF (once, slow):  connect -> send_raf -> get_profile
    change recipe (often):  apply_recipe(base, recipe) -> set_profile
                            -> trigger_conversion -> wait_for_result
    quit:                   disconnect

"""

from __future__ import annotations

import contextlib
import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import rawji
import usb.core
from rawji.fuji_enums import (
    FUJIFILM_USB_VENDOR_ID,
    ChromeEffect,
    ColorSpace,
    GrainEffect,
    GrainEffectSize,
    ev_to_int,
    int_to_ev,
    validate_color_temp,
    validate_wb_shift,
)
from rawji.fuji_profile import (
    INDEX_TO_PARAM,
    PROFILE_PARAMS_OFFSET,
    TONE_PARAMS,
    decode_noise_reduction,
    decode_tone_value,
    encode_noise_reduction,
    encode_tone_value,
)

from grawji.recipe import Recipe

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

# The film-simulation byte sits at its own parameter offset in rawji's layout.
OFFSET_FILM_SIM = _PARAM_OFFSETS["FilmSimulation"]

# Film-simulation codes at @541. rawji's enum is correct up to Eterna (16),
# but its EternaBleach=17 is wrong and it lacks the newer sims entirely.
# Hardware-verified on the X-E5 (render signatures, plus the camera's own
# profile carrying 20 for a RAF whose EXIF says REALA ACE):
#   17=Classic Neg, 18=Eterna Bleach Bypass, 19=Nostalgic Neg, 20=Reala Ace.
# The engine renders any out-of-range code as Provia.
# todo: fix that in rawji
FILM_SIM_CODES: dict[str, int] = {e.name: int(e) for e in rawji.FilmSimulation}
FILM_SIM_CODES.update(
    {"ClassicNeg": 17, "EternaBleach": 18, "NostalgicNeg": 19, "RealaAce": 20}
)
_FILM_SIM_NAMES = {v: k for k, v in FILM_SIM_CODES.items()}

# Params that use the value*10 tone encoding. rawji's TONE_PARAMS already
# excludes NoiseReduction.
_TONE_PARAMS = frozenset(TONE_PARAMS)

# Parameters that only exist on bodies with a long-enough profile (the
# high-index effect slots). On shorter profiles (X100F 601 B, X-T3 605 B)
# their offset overruns the profile, so they are skipped rather than fatal.
_OPTIONAL_PARAMS = frozenset(
    {"Clarity", "ColorChromeBlue", "SmoothSkinEffect"}
)

_CLARITY_LIMIT = 5

# Default wait_for_result timeout, in seconds.
DEFAULT_TIMEOUT = 30

# How long a USB device reset needs before the camera answers again.
_USB_RESET_SETTLE_S = 2.0


def _reset_camera_usb() -> None:
    """Reset the camera's USB device to recover a wedged interface.

    A crashed session can leave the camera's PTP interface unclaimable:
    claiming fails with "Entity not found" or writes time out,
    even though the device still enumerates.
    A device-level USB reset re-enumerates it into a claimable state again,
    so open() tries one reset before giving up.
    Any failure here is swallowed, the retry's connect reports the error.
    """
    with contextlib.suppress(Exception):
        device = usb.core.find(idVendor=FUJIFILM_USB_VENDOR_ID)
        if device is not None:
            device.reset()
            time.sleep(_USB_RESET_SETTLE_S)


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
    """Return the profile byte for a film-simulation name.

    Args:
        name: Film-simulation name, e.g. "Velvia" or "RealaAce".

    Returns:
        The byte value written at OFFSET_FILM_SIM.

    Raises:
        ValueError: If the name is not a known film simulation.
    """
    try:
        return FILM_SIM_CODES[name]
    except KeyError as e:
        msg = f"unknown film simulation: {name}"
        raise ValueError(msg) from e


def _clamp_clarity(value: int) -> int:
    """Clamp clarity into the honoured -5..+5 range."""
    return max(-_CLARITY_LIMIT, min(_CLARITY_LIMIT, value))


def _grain_code(effect: str, size: str) -> int:
    """Combine grain effect and size into the single @545 profile code."""
    if effect == "Off":
        return int(GrainEffect.Off)
    base = _enum_value(GrainEffect, effect, "grain effect")
    large = _enum_value(GrainEffectSize, size, "grain size") == int(
        GrainEffectSize.Large
    )
    return base + (2 if large else 0)


def _grain_effect_name(code: int, fallback: str) -> str:
    """Decode the grain effect from the combined @545 code (_grain_code)."""
    return {2: "Weak", 3: "Strong", 4: "Weak", 5: "Strong"}.get(
        code, "Off" if code == int(GrainEffect.Off) else fallback
    )


def _grain_size_name(code: int, fallback: str) -> str:
    """Decode the grain size from the combined @545 code (see _grain_code)."""
    if code in (4, 5):
        return "Large"
    if code in (int(GrainEffect.Off), 2, 3):
        return "Small"
    return fallback


def recipe_changes(recipe: Recipe) -> dict[str, float]:
    """Map a recipe to rawji profile parameter values (validated).

    Args:
        recipe: The recipe to translate.

    Returns:
        A dict of rawji parameter name -> value. Tone values are the raw
        user values here (possibly half steps); encoding to the integer
        profile representation happens in apply_recipe().

    Raises:
        ValueError: If a name is unknown or a tone value is out of range.
    """
    film_sim = film_simulation_byte(recipe.film_simulation)
    rawji.validate_params(
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
        # Grain effect and size share one slot (@545); see _grain_code.
        "GrainEffect": _grain_code(recipe.grain, recipe.grain_size),
        "ColorChromeEffect": _enum_value(
            ChromeEffect, recipe.color_chrome, "color chrome"
        ),
        "SmoothSkinEffect": _enum_value(
            ChromeEffect, recipe.smooth_skin, "smooth skin"
        ),
        "ColorChromeBlue": _enum_value(
            ChromeEffect, recipe.color_chrome_blue, "color chrome blue"
        ),
        "Clarity": _clamp_clarity(recipe.clarity),
        "HighlightTone": recipe.highlights,
        "ShadowTone": recipe.shadows,
        "Color": recipe.color,
        "Sharpness": recipe.sharpness,
        "NoiseReduction": encode_noise_reduction(recipe.noise_reduction),
        "ColorSpace": _enum_value(
            ColorSpace, recipe.color_space, "colour space"
        ),
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
            if name in _OPTIONAL_PARAMS:
                continue
            msg = f"profile too short ({len(base)} bytes) for {name}"
            raise ValueError(msg)
        # Tone values may be half steps (0.5 -> raw 5), so round after
        # the *10 encoding rather than truncating.
        encoded = (
            round(encode_tone_value(value))
            if name in _TONE_PARAMS
            else int(value)
        )
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


def _decode_half_tone(raw: int) -> float:
    """Decode a *10 tone raw value keeping 0.5 steps.

    rawji's decode_tone_value floor-divides and destroys halves
    (raw 5 -> 0 instead of 0.5); this snaps to the nearest half.
    """
    return round(raw / 5) / 2


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
        film_simulation=_FILM_SIM_NAMES.get(
            signed("FilmSimulation", 1), defaults.film_simulation
        ),
        white_balance=_enum_name(
            rawji.WhiteBalance, signed("WhiteBalance"), defaults.white_balance
        ),
        dynamic_range=_enum_name(
            rawji.DynamicRange,
            signed("DynamicRange", 1),
            defaults.dynamic_range,
        ),
        grain=_grain_effect_name(signed("GrainEffect", 1), defaults.grain),
        grain_size=_grain_size_name(
            signed("GrainEffect", 1), defaults.grain_size
        ),
        color_chrome=_enum_name(
            ChromeEffect,
            signed("ColorChromeEffect", 1),
            defaults.color_chrome,
        ),
        color_chrome_blue=_enum_name(
            ChromeEffect,
            signed("ColorChromeBlue", 1),
            defaults.color_chrome_blue,
        ),
        clarity=decode_tone_value(signed("Clarity")),
        smooth_skin=_enum_name(
            ChromeEffect, signed("SmoothSkinEffect", 1), defaults.smooth_skin
        ),
        exposure=int_to_ev(signed("ExposureBias")),
        highlights=_decode_half_tone(signed("HighlightTone")),
        shadows=_decode_half_tone(signed("ShadowTone")),
        color=decode_tone_value(signed("Color")),
        sharpness=decode_tone_value(signed("Sharpness")),
        noise_reduction=decode_noise_reduction(
            signed("NoiseReduction", 0x2000)
        ),
        wb_shift_r=signed("WBShiftR"),
        wb_shift_b=signed("WBShiftB"),
        color_temp=color_temp if color_temp > 0 else defaults.color_temp,
        color_space=_enum_name(
            ColorSpace, signed("ColorSpace", 1), defaults.color_space
        ),
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
        usb_reset: Callable[[], None] | None = None,
    ) -> None:
        """Create a session.

        Args:
            camera_factory: Callable returning a fresh camera object.
                Defaults to rawji.FujiCamera. Injectable for tests.
            timeout: wait_for_result timeout in seconds.
            usb_reset: Recovery hook run when a connect fails, before the
                one retry. Defaults to a USB device reset, injectable so
                tests never reset real hardware.
        """
        self._camera_factory: Callable[[], Any] = (
            camera_factory or rawji.FujiCamera
        )
        self._usb_reset = (
            usb_reset if usb_reset is not None else _reset_camera_usb
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
            try:
                camera, base = self._open_attempt(raf_path)
            except ForeignRafError:
                raise  # the camera is fine; a reset would not help
            except Exception:
                # A wedged USB interface (failed claim, bulk-write
                # timeout) often recovers with one device reset; retry
                # the whole open once after it. Opening is safe to retry
                # - no conversion is in flight yet.
                self._usb_reset()
                camera, base = self._open_attempt(raf_path)
            self._camera = camera
            self._base_profile = base
            self._raf_path = Path(raf_path)

    def _open_attempt(self, raf_path: str | Path) -> tuple[Any, bytes]:
        """Run one connect / send_raf / get_profile sequence."""
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
        return camera, base

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
