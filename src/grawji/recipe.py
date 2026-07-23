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
        grain_size: Grain size: "Small" or "Large" (ignored when grain is
            "Off"; folded into the grain profile slot).
        color_chrome: Color Chrome Effect: "Off", "Weak" or "Strong".
        color_chrome_blue: Color Chrome FX Blue: "Off", "Weak" or "Strong".
            Only honoured on bodies whose profile is long enough (offset
            609); ignored on older, shorter profiles.
        exposure: Exposure compensation in EV, -2.0 to +3.0 (1/3 steps).
        highlights: Highlight tone, -2 to +4. XProcessor5 bodies honour
            0.5 steps, older bodies integer steps only.
        shadows: Shadow tone, -2 to +4 (0.5 steps on XProcessor5).
        color: Colour / saturation, -4 to +4.
        sharpness: Sharpness, -4 to +4.
        noise_reduction: Noise reduction, -4 to +4.
        clarity: Clarity, -5 to +5. Only honoured on bodies whose profile
            is long enough (offset 617); ignored on older profiles.
        smooth_skin: Smooth Skin Effect: "Off", "Weak" or "Strong". Only
            honoured on bodies whose profile is long enough (offset 605).
        wb_shift_r: White-balance red shift, -9 to +9.
        wb_shift_b: White-balance blue shift, -9 to +9.
        color_temp: White-balance colour temperature in kelvin,
            2500 to 10000 (only applied when white_balance is
            "Temperature").
        color_space: Export colour space, "sRGB" or "AdobeRGB".
        mono_warm_cool: Monochromatic Color warm-cool toning for B&W film
            sims, camera units (negative cool, positive warm). Only applied
            when the film simulation is Acros or Monochrome.
        mono_magenta_green: Monochromatic Color magenta-green toning for B&W
            film sims (negative magenta, positive green). XProcessor5 only.
        origin_body: Model the recipe was authored on (e.g. "X-T3"), or ""
            if unknown. Captured when saved with a body connected, used only
            to badge/group recipes, never affects rendering.
    """

    film_simulation: str = "Provia"
    white_balance: str = "AsShot"
    dynamic_range: str = "DR100"
    grain: str = "Off"
    grain_size: str = "Small"
    color_chrome: str = "Off"
    color_chrome_blue: str = "Off"
    exposure: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    color: int = 0
    sharpness: int = 0
    noise_reduction: int = 0
    clarity: int = 0
    smooth_skin: str = "Off"
    wb_shift_r: int = 0
    wb_shift_b: int = 0
    color_temp: int = 5500
    color_space: str = "sRGB"
    mono_warm_cool: int = 0
    mono_magenta_green: int = 0
    origin_body: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return a plain dict suitable for JSON recipe storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Recipe:
        """Build a Recipe from a recipe dict, ignoring extras."""
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)  # type: ignore[arg-type]
