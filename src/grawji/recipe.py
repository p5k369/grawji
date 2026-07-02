"""Recipe data model - a pure, testable description of a Fuji look."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Recipe:
    """Recipe parameters applied on top of a RAF's own profile.

    Enum-valued fields take a rawji enum member name; the tone fields are
    small signed integers in the ranges the camera accepts. Every field
    here maps to a rawji profile parameter whose offset and encoding were
    verified against real hardware.

    Attributes:
        film_simulation: Film simulation, e.g. "Velvia".
        white_balance: White-balance mode, e.g. "Daylight". Use
            "Temperature" to drive color_temp.
        dynamic_range: Dynamic range: "DR100", "DR200" or
            "DR400".
        grain: Grain effect: "Off", "Weak" or "Strong".
        color_chrome: Color Chrome Effect: "Off", "Weak" or "Strong".
        exposure: Exposure compensation in EV, -2.0 to +3.0 (1/3 steps).
        highlights: Highlight tone, -2 to +4.
        shadows: Shadow tone, -2 to +4.
        color: Colour / saturation, -4 to +4.
        sharpness: Sharpness, -4 to +4.
        noise_reduction: Noise reduction, -4 to +4.
        wb_shift_r: White-balance red shift, -9 to +9.
        wb_shift_b: White-balance blue shift, -9 to +9.
        color_temp: White-balance colour temperature in kelvin,
            2500 to 10000 (only applied when white_balance is
            "Temperature").
        color_space: Export colour space, "sRGB" or "AdobeRGB".
    """

    film_simulation: str = "Provia"
    white_balance: str = "AsShot"
    dynamic_range: str = "DR100"
    grain: str = "Off"
    color_chrome: str = "Off"
    exposure: float = 0.0
    highlights: int = 0
    shadows: int = 0
    color: int = 0
    sharpness: int = 0
    noise_reduction: int = 0
    wb_shift_r: int = 0
    wb_shift_b: int = 0
    color_temp: int = 5500
    color_space: str = "sRGB"

    def to_dict(self) -> dict[str, object]:
        """Return a plain dict suitable for JSON recipe storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Recipe:
        """Build a Recipe from a recipe dict, ignoring extras."""
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)  # type: ignore[arg-type]
