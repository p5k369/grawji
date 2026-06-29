"""Thin adapter around the rawji library.

Implements the "load once, render many" workflow and grawji's own
read-modify-write (RMW) profile strategy:

    open RAF (once, slow):  connect -> send_raf -> get_profile
    change recipe (often):  rmw_patch(base, recipe) -> set_profile
                            -> trigger_conversion -> wait_for_result
    quit:                   disconnect

Order matters: ``send_raf`` *before* ``get_profile``; profile-set
*before* trigger. ``send_raf`` runs only on open, never per slider move.

All camera calls block for seconds and must run off the GTK main thread
(see :mod:`grawji.preview`); only one camera op may be in flight at a
time.
"""

from __future__ import annotations

# Verified profile byte offsets (X100F / X-T3). Do not add unverified
# offsets here without a passing mini-test - see the project notes.
OFFSET_FILM_SIM = 541
OFFSET_IMAGE_SIZE = 521
OFFSET_QUALITY = 525


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
