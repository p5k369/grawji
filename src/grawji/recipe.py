"""Recipe data model - a pure, testable description of a Fuji look.

This module is deliberately free of GTK and rawji imports so the recipe
logic (defaults, validation, serialisation) can be unit-tested in
isolation. The adapter in :mod:`grawji.core` maps a :class:`Recipe` onto
the bytes of a native camera profile (read-modify-write).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Recipe:
    """A set of recipe parameters applied on top of a RAF's own profile.

    Only fields that map to *verified* profile offsets should be added
    here. Unverified offsets (highlights, shadows, colour, sharpness,
    grain, DR, WB) stay out until each has a passing mini-test.

    Attributes:
        film_simulation: Film-simulation name, e.g. ``"Velvia"``.
        image_size: Output image size token, or ``None`` to inherit the
            RAF's own value.
        quality: Output quality token, or ``None`` to inherit the RAF's
            own value.
    """

    film_simulation: str = "Provia"
    image_size: str | None = None
    quality: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a plain dict suitable for JSON preset storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Recipe:
        """Build a :class:`Recipe` from a stored preset dict."""
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)  # type: ignore[arg-type]
