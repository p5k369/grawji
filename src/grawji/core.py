"""Thin adapter around the rawji library.

Implements the "load once, render many" workflow and grawji's own
read-modify-write (RMW) profile strategy:

    open RAF (once, slow):  connect -> send_raf -> get_profile
    change recipe (often):  apply_recipe(base, recipe) -> set_profile
                            -> trigger_conversion -> wait_for_result
    quit:                   disconnect

Order matters: ``send_raf`` *before* ``get_profile``; profile-set
*before* trigger. ``send_raf`` runs only on open, never per slider move.

"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import rawji

from grawji.recipe import Recipe

# Verified profile byte offsets (X100F / X-T3). Do not add unverified
# offsets here without a passing mini-test - see the project notes.
OFFSET_FILM_SIM = 541
OFFSET_IMAGE_SIZE = 521
OFFSET_QUALITY = 525

# Default wait_for_result timeout, in seconds.
DEFAULT_TIMEOUT = 30


class CameraError(RuntimeError):
    """A camera / PTP operation failed."""


class ForeignRafError(CameraError):
    """The RAF was shot by a different body (PTP error 0x2002).

    Fuji cameras only convert their own RAFs; sending a foreign file
    makes ``get_profile`` fail with ``0x2002``.
    """


class SessionStateError(CameraError):
    """A render was requested before a RAF was opened."""


def rmw_patch(base: bytes, film_sim_byte: int) -> bytes:
    """Read-modify-write a native camera profile in place.

    Patches only the verified bytes, leaving the RAF's own recipe
    intact. This is intentionally a small, dependency-free helper so it
    can be unit-tested without a camera.

    Args:
        base: The profile bytes read from the camera via ``get_profile``.
        film_sim_byte: The film-simulation byte to write at
            :data:`OFFSET_FILM_SIM`.

    Returns:
        A new ``bytes`` object with the patched profile.

    Raises:
        ValueError: If ``base`` is too short to hold the offset.
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


def film_simulation_byte(name: str) -> int:
    """Return the profile byte for a film-simulation name.

    Args:
        name: Film-simulation name, e.g. ``"Velvia"`` (resolved by
            rawji's ``FilmSimulation.from_name``).

    Returns:
        The byte value written at :data:`OFFSET_FILM_SIM`.

    Raises:
        ValueError: If the name is not a known film simulation.
    """
    return int(rawji.FilmSimulation.from_name(name))


def apply_recipe(base: bytes, recipe: Recipe) -> bytes:
    """Apply a recipe to a native camera profile (read-modify-write).

    Patches only verified bytes, leaving the RAF's own recipe intact.

    Args:
        base: Profile bytes read from the camera.
        recipe: The recipe to apply.

    Returns:
        A new profile with the recipe's film simulation patched in.

    Raises:
        NotImplementedError: If the recipe sets ``image_size`` or
            ``quality`` - the offsets (521/525) are known but the byte
            encoding is not yet verified, so patching them is not wired
            up.
        ValueError: If the film-simulation name is unknown or the
            profile is too short.
    """
    if recipe.image_size is not None or recipe.quality is not None:
        msg = (
            "image_size/quality patching is not wired up yet "
            "(offsets 521/525 known, byte encoding unverified)"
        )
        raise NotImplementedError(msg)
    return rmw_patch(base, film_simulation_byte(recipe.film_simulation))


class CameraSession:
    """A "load once, render many" session around ``rawji.FujiCamera``.

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
                Defaults to ``rawji.FujiCamera``. Injectable for tests.
            timeout: ``wait_for_result`` timeout in seconds.
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

    def open(self, raf_path: str | Path) -> None:
        """Connect and load a RAF (slow; call once per image).

        Order matters: ``send_raf`` must precede ``get_profile`` so the
        camera reports a valid profile. Any previously open session is
        closed first.

        Args:
            raf_path: Path to the RAF file. Must be from the connected
                body, or the camera fails with ``0x2002``.

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
                        "RAF was shot by a different camera body "
                        "(PTP 0x2002)"
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
            full_resolution: ``False`` for a fast preview (ignores
                profile size), ``True`` for a full-resolution export.

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
        if self._camera is not None:
            self._safe_disconnect(self._camera)
        self._camera = None
        self._base_profile = None
        self._raf_path = None

    @staticmethod
    def _safe_disconnect(camera: Any) -> None:
        with contextlib.suppress(Exception):
            camera.disconnect()

    def __enter__(self) -> CameraSession:
        """Enter the runtime context, returning the session."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the session on context exit."""
        self.close()
