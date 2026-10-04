"""The recipe a Fujifilm photo was shot with, from its makernote."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from grawji.recipe import Recipe

# Reads one tag, Exif.Fujifilm.<name> or a full key.
TagReader = Callable[[str], str | None]

_FILM_MODES = {
    0x000: "Provia",
    0x120: "Astia",
    0x200: "Velvia",
    0x400: "Velvia",
    0x500: "ProNegStd",
    0x501: "ProNegHi",
    0x600: "ClassicChrome",
    0x700: "Eterna",
    0x800: "ClassicNeg",
    0x900: "EternaBleach",
    0xA00: "NostalgicNeg",
    0xB00: "RealaAce",
}
# The B&W sims are recorded in the saturation tag, not in FilmMode.
_MONO_SIMS = {
    0x300: "Monochrome",
    0x301: "MonochromeR",
    0x302: "MonochromeYe",
    0x303: "MonochromeG",
    0x310: "Sepia",
    0x500: "Acros",
    0x501: "AcrosR",
    0x502: "AcrosYe",
    0x503: "AcrosG",
}
_COLOR = {
    0x000: 0,
    0x080: 1,
    0x100: 2,
    0x0C0: 3,
    0x0E0: 4,
    0x180: -1,
    0x200: -2,
    0x400: -2,
    0x4C0: -3,
    0x4E0: -4,
}
_SHARPNESS = {
    0x00: -4,
    0x01: -3,
    0x02: -2,
    0x82: -1,
    0x03: 0,
    0x84: 1,
    0x04: 2,
    0x05: 3,
    0x06: 4,
}
_NOISE_REDUCTION = {
    0x000: 0,
    0x180: 1,
    0x100: 2,
    0x1C0: 3,
    0x1E0: 4,
    0x280: -1,
    0x200: -2,
    0x2C0: -3,
    0x2E0: -4,
}
# The two extra auto modes render like plain Auto in RAW conversion.
_WHITE_BALANCE = {
    0x000: "Auto",
    0x001: "Auto",
    0x002: "Auto",
    0x100: "Daylight",
    0x200: "Shade",
    0x300: "Fluorescent1",
    0x301: "Fluorescent2",
    0x302: "Fluorescent3",
    0x400: "Incandescent",
    0x600: "Underwater",
    0xF00: "Custom1",
    0xF01: "Custom2",
    0xF02: "Custom3",
    0xFF0: "Temperature",
}
_KELVIN = 0xFF0
_STRENGTH = {0: "Off", 32: "Weak", 64: "Strong"}
_GRAIN_SIZE = {16: "Small", 32: "Large"}
_DR_AUTO = 0
_DR_MANUAL = 1
_DR_PERCENT = {100: "DR100", 200: "DR200", 400: "DR400"}
# Tones are stored as minus sixteen per step, half steps included.
_TONE_STEP = -16
_CLARITY_STEP = 1000
# White balance shift: twenty per step, confirmed on the X-E5.
_SHIFT_STEP = 20
_ADOBE_RGB = 0xFFFF


@dataclass(frozen=True)
class PhotoRecipe:
    """A recipe read from a photo, with what could not be read."""

    recipe: Recipe
    missing: list[str] = field(default_factory=list)


def _number(read: TagReader, name: str) -> int | None:
    """One tag as an integer."""
    key = name if name.startswith("Exif.") else f"Exif.Fujifilm.{name}"
    text = read(key)
    if not text:
        return None
    try:
        return int(text.split()[0])
    except ValueError:
        return None


def _film_simulation(read: TagReader) -> str | None:
    """The film simulation, the B&W ones from the saturation tag."""
    color = _number(read, "Color")
    if color in _MONO_SIMS:
        return _MONO_SIMS[color]
    mode = _number(read, "FilmMode")
    return _FILM_MODES.get(mode) if mode is not None else None


def _white_balance(read: TagReader, values: dict[str, Any]) -> None:
    """The white balance mode."""
    mode = _number(read, "WhiteBalance")
    if mode in _WHITE_BALANCE:
        values["white_balance"] = _WHITE_BALANCE[mode]
        kelvin = _number(read, "ColorTemperature")
        if mode == _KELVIN and kelvin:
            values["color_temp"] = kelvin
    shift = read("Exif.Fujifilm.WhiteBalanceFineTune")
    if shift:
        try:
            red, blue = (int(v) for v in shift.split()[:2])
        except ValueError:
            return
        values["wb_shift_r"] = round(red / _SHIFT_STEP)
        values["wb_shift_b"] = round(blue / _SHIFT_STEP)


def _dynamic_range(read: TagReader) -> str | None:
    """The DR setting, Auto when the camera chose it per shot."""
    setting = _number(read, "DynamicRangeSetting")
    if setting == _DR_AUTO:
        return "Auto"
    if setting == _DR_MANUAL:
        developed = _number(read, "DevelopmentDynamicRange")
        return _DR_PERCENT.get(developed) if developed else None
    return None


def _tables(read: TagReader, values: dict[str, Any]) -> None:
    """The fields that map one code to one value."""
    color = _number(read, "Color")
    if color in _COLOR:
        values["color"] = _COLOR[color]
    for name, tag, table in (
        ("sharpness", "Sharpness", _SHARPNESS),
        ("noise_reduction", "HighIsoNoiseReduction", _NOISE_REDUCTION),
        ("color_chrome", "ColorChromeEffect", _STRENGTH),
        ("color_chrome_blue", "ColorChromeFXBlue", _STRENGTH),
    ):
        raw = _number(read, tag)
        if raw is not None and raw in table:
            values[name] = table[raw]
    grain = _number(read, "GrainEffectRoughness")
    if grain in _STRENGTH:
        values["grain"] = _STRENGTH[grain]
        size = _number(read, "GrainEffectSize")
        if size in _GRAIN_SIZE:
            values["grain_size"] = _GRAIN_SIZE[size]


def _scaled(read: TagReader, values: dict[str, Any]) -> None:
    """Tones, clarity and B&W toning, stored as scaled integers."""
    for name, tag in (
        ("highlights", "HighlightTone"),
        ("shadows", "ShadowTone"),
    ):
        raw = _number(read, tag)
        if raw is not None:
            values[name] = raw / _TONE_STEP
    clarity = _number(read, "Clarity")
    if clarity is not None:
        values["clarity"] = round(clarity / _CLARITY_STEP)
    for name, tag in (
        ("mono_warm_cool", "BWAdjustment"),
        ("mono_magenta_green", "BWMagentaGreen"),
    ):
        raw = _number(read, tag)
        if raw:
            values[name] = raw


def recipe_from_tags(read: TagReader) -> PhotoRecipe | None:
    """The recipe a photo's tags describe."""
    film = _film_simulation(read)
    if film is None:
        return None
    values: dict[str, Any] = {"film_simulation": film}
    _tables(read, values)
    _scaled(read, values)
    _white_balance(read, values)
    dynamic_range = _dynamic_range(read)
    if dynamic_range is not None:
        values["dynamic_range"] = dynamic_range
    if _number(read, "Exif.Photo.ColorSpace") == _ADOBE_RGB:
        values["color_space"] = "AdobeRGB"
    values["origin_body"] = (read("Exif.Image.Model") or "").strip()
    missing = [
        name
        for name in ("white_balance", "dynamic_range")
        if name not in values
    ]
    return PhotoRecipe(Recipe(**values), missing)
