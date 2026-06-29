"""Recipe data model - a pure, testable description of a Fuji look."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Recipe:
    """Recipe parameters applied on top of a RAF's own profile.

    Enum-valued fields take a rawji enum member name; the tone fields are
    small signed integers in the ranges the camera accepts. Only fields
    that map to a rawji profile parameter with a known encoding are here
    (exposure is held back until its profile encoding is calibrated on
    real hardware).

    Attributes:
        film_simulation: Film simulation, e.g. ``"Velvia"``.
        white_balance: White-balance mode, e.g. ``"Daylight"``.
        dynamic_range: Dynamic range: ``"DR100"``, ``"DR200"`` or
            ``"DR400"``.
        highlights: Highlight tone, ``-4..+4``.
        shadows: Shadow tone, ``-2..+4``.
        color: Colour / saturation, ``-4..+4``.
        sharpness: Sharpness, ``-4..+4``.
    """

    film_simulation: str = "Provia"
    white_balance: str = "AsShot"
    dynamic_range: str = "DR100"
    highlights: int = 0
    shadows: int = 0
    color: int = 0
    sharpness: int = 0

    def to_dict(self) -> dict[str, object]:
        """Return a plain dict suitable for JSON preset storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Recipe:
        """Build a :class:`Recipe` from a preset dict, ignoring extras."""
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)  # type: ignore[arg-type]
