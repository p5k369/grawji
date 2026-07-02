"""Import Fujifilm X RAW Studio FP1/FP2/FP3 recipes into a grawji Recipe.

X RAW Studio (and petabyt's fp tool) store conversion recipes as small XML
files with their own string-token vocabulary. This module reads that
vocabulary and maps it onto grawji's Recipe, which speaks rawji enum member
names. Only the parameters grawji models are read, the rest (clarity,
Color Chrome FX Blue, monochrome warmth, lens-modulation, ...) is ignored,
so a round-trip is lossy by design.

The token tables come from fp's data.c and were checked against its sample
files. See github.com/petabyt/fp for the authoritative field encodings.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from grawji.recipe import Recipe

# FP film-simulation token -> rawji FilmSimulation member name.
_FILM_SIM = {
    "Provia": "Provia",
    "Velvia": "Velvia",
    "Astia": "Astia",
    "Classic": "ClassicChrome",
    "NEGAStd": "ProNegStd",
    "NEGAhi": "ProNegHi",
    "Acros": "Acros",
    "AcrosYe": "AcrosYe",
    "AcrosR": "AcrosR",
    "AcrosG": "AcrosG",
    "Eterna": "Eterna",
    "BW": "Monochrome",
    "BYe": "MonochromeYe",
    "BR": "MonochromeR",
    "BG": "MonochromeG",
    "Sepia": "Sepia",
    "NostalgicNEGA": "ClassicChrome",
}

# FP white-balance token -> rawji WhiteBalance member name.
_WHITE_BALANCE = {
    "AsShot": "AsShot",
    "INVALID": "AsShot",
    "Auto": "Auto",
    "Temperature": "Temperature",
    "Daylight": "Daylight",
    "Incandescent": "Incandescent",
    "Underwater": "Underwater",
    "Shade": "Shade",
    "FLight1": "Fluorescent1",
    "FLight2": "Fluorescent2",
    "FLight3": "Fluorescent3",
    "Custom1": "Custom1",
    "Custom2": "Custom2",
    "Custom3": "Custom3",
}

_GRAIN = {"OFF": "Off", "WEAK": "Weak", "STRONG": "Strong"}
_CHROME = {"OFF": "Off", "WEAK": "Weak", "STRONG": "Strong"}
_DYNAMIC_RANGE = {"100": "DR100", "200": "DR200", "400": "DR400"}
_COLOR_SPACE = {"sRGB": "sRGB", "AdobeRGB": "AdobeRGB"}
# Two-digit thirds in an exposure token map to 0, 1/3 or 2/3 of an EV.
_THIRDS = {"": 0, "0": 0, "00": 0, "33": 1, "67": 2}
# Inverse tables for export.
_FILM_SIM_OUT = {
    "Provia": "Provia",
    "Velvia": "Velvia",
    "Astia": "Astia",
    "ClassicChrome": "Classic",
    "ProNegStd": "NEGAStd",
    "ProNegHi": "NEGAhi",
    "Acros": "Acros",
    "AcrosYe": "AcrosYe",
    "AcrosR": "AcrosR",
    "AcrosG": "AcrosG",
    "Eterna": "Eterna",
    "EternaBleach": "Eterna",
    "Monochrome": "BW",
    "MonochromeYe": "BYe",
    "MonochromeR": "BR",
    "MonochromeG": "BG",
    "Sepia": "Sepia",
}
_WHITE_BALANCE_OUT = {
    "AsShot": "AsShot",
    "Auto": "Auto",
    "Temperature": "Temperature",
    "Daylight": "Daylight",
    "Incandescent": "Incandescent",
    "Underwater": "Underwater",
    "Shade": "Shade",
    "Fluorescent1": "FLight1",
    "Fluorescent2": "FLight2",
    "Fluorescent3": "FLight3",
    "Custom1": "Custom1",
    "Custom2": "Custom2",
    "Custom3": "Custom3",
}
_GRAIN_OUT = {v: k for k, v in _GRAIN.items()}
_CHROME_OUT = {v: k for k, v in _CHROME.items()}
_DYNAMIC_RANGE_OUT = {v: k for k, v in _DYNAMIC_RANGE.items()}
# EV in thirds (round(ev * 3)) -> FP exposure-bias token, matching fp's exact
# spellings so the file round-trips through fp as well as grawji.
_EXPOSURE_OUT = {
    9: "P3P00",
    8: "P2P67",
    7: "P2P33",
    6: "P2P0",
    5: "P1P67",
    4: "P1P33",
    3: "P1P00",
    2: "P0P67",
    1: "P0P33",
    0: "0",
    -1: "M0P33",
    -2: "M0P67",
    -3: "M1P00",
    -4: "M1P33",
    -5: "M1P67",
    -6: "M2P00",
    -7: "M2P33",
    -8: "M2P67",
    -9: "M3P00",
}
# X-T3 processor code, used when no live profile supplies one on export.
_DEFAULT_IOPCODE = 0xFF159501


def _exposure_ev(bias: str) -> float:
    """Decode an FP exposure-bias token (e.g. "M1P33") to an EV float.

    A token is a sign letter (P plus, M minus), the whole EV, then a
    two-digit third (00, 33 or 67); the bare "0" is zero.

    Args:
        bias: The FP ExposureBias token.

    Returns:
        The exposure compensation in EV, or 0.0 if the token is unknown.
    """
    bias = bias.strip()
    if not bias or bias == "0":
        return 0.0
    sign = 1.0
    if bias[0] in "PM":
        sign = -1.0 if bias[0] == "M" else 1.0
        bias = bias[1:]
    whole, _, frac = bias.partition("P")
    try:
        ev = int(whole) + _THIRDS.get(frac, 0) / 3
    except ValueError:
        return 0.0
    return sign * ev


def parse_fp(text: str) -> Recipe:
    """Parse an X RAW Studio FP recipe into a grawji Recipe.

    Args:
        text: The contents of an FP1, FP2 or FP3 file. A leading byte-order
            mark is tolerated.

    Returns:
        A Recipe with the FP file's modelled parameters; fields grawji does
        not model, and any the file omits, keep their Recipe defaults.

    Raises:
        ValueError: If the text is not a valid FP conversion profile.
    """
    # The FP file is a small local recipe the user explicitly opened, so
    # the untrusted-XML risk is the same as the JSON recipe import.
    try:
        root = ET.fromstring(text.lstrip("﻿").encode("utf-8"))  # noqa: S314
    except ET.ParseError as e:
        msg = "not a valid FP recipe file (malformed XML)"
        raise ValueError(msg) from e
    if root.tag != "ConversionProfile":
        msg = "not an FP recipe file (missing ConversionProfile root)"
        raise ValueError(msg)
    group = root.find("PropertyGroup")
    source = group if group is not None else root
    fields = {child.tag: (child.text or "").strip() for child in source}

    def integer(tag: str, default: int = 0) -> int:
        try:
            return int(fields.get(tag, ""))
        except ValueError:
            return default

    defaults = Recipe()
    temp_token = fields.get("WBColorTemp", "").rstrip("Kk")  # "<kelvin>K".
    try:
        color_temp = int(temp_token)
    except ValueError:
        color_temp = 0

    return Recipe(
        film_simulation=_FILM_SIM.get(
            fields.get("FilmSimulation", ""), defaults.film_simulation
        ),
        white_balance=_WHITE_BALANCE.get(
            fields.get("WhiteBalance", ""), defaults.white_balance
        ),
        dynamic_range=_DYNAMIC_RANGE.get(
            fields.get("DynamicRange", ""), defaults.dynamic_range
        ),
        grain=_GRAIN.get(fields.get("GrainEffect", ""), defaults.grain),
        color_chrome=_CHROME.get(
            fields.get("ChromeEffect", ""), defaults.color_chrome
        ),
        exposure=_exposure_ev(fields.get("ExposureBias", "0")),
        highlights=integer("HighlightTone"),
        shadows=integer("ShadowTone"),
        color=integer("Color"),
        sharpness=integer("Sharpness"),
        # X RAW Studio misspells the tag "NoisReduction", keep it verbatim.
        noise_reduction=integer("NoisReduction"),
        wb_shift_r=integer("WBShiftR"),
        wb_shift_b=integer("WBShiftB"),
        color_temp=color_temp if color_temp > 0 else defaults.color_temp,
        color_space=_COLOR_SPACE.get(
            fields.get("ColorSpace", ""), defaults.color_space
        ),
    )


def _exposure_token(ev: float) -> str:
    """Encode an EV float as an FP exposure-bias token (e.g. "M1P33")."""
    thirds = max(-9, min(9, round(ev * 3)))
    return _EXPOSURE_OUT[thirds]


def serialize_fp(
    recipe: Recipe,
    *,
    iopcode: int | None = None,
    label: str = "",
    device: str = "X-T3",
    device_version: str = "X-T3_0100",
) -> str:
    """Render a grawji Recipe as an X RAW Studio FP conversion profile.

    The output is a complete ConversionProfile that grawji and
    petabyt's fp can both read back. Parameters grawji does not model are
    written with neutral defaults, so a grawji round-trip is faithful while
    the dropped effects (clarity, Color Chrome FX Blue, ...) stay off.

    Args:
        recipe: The recipe to serialise.
        iopcode: The processor code to stamp; defaults to the X-T3's when a
            live profile does not supply one.
        label: The recipe name to record in the PropertyGroup.
        device: The camera model name for the PropertyGroup attribute.
        device_version: The firmware tag for the PropertyGroup attribute.

    Returns:
        The FP file contents as a string (UTF-8, with an XML declaration).
    """
    is_temp = recipe.white_balance == "Temperature"
    code = iopcode if iopcode is not None else _DEFAULT_IOPCODE
    # PropertyGroup children in X RAW Studio's order. A None value is written
    # as an empty element, which is how X RAW Studio records an unset effect;
    # the effects grawji does not model stay neutral that way.
    fields: list[tuple[str, str | None]] = [
        ("SerialNumber", None),
        ("TetherRAWConditonCode", None),
        ("Editable", "TRUE"),
        ("SourceFileName", None),
        ("Fileerror", "NONE"),
        ("RotationAngle", "0"),
        ("StructVer", "65536"),
        ("IOPCode", f"{code:08X}"),
        ("ShootingCondition", "OFF"),
        ("FileType", "JPG"),
        ("ImageSize", "L3x2"),
        ("ImageQuality", "Fine"),
        ("ExposureBias", _exposure_token(recipe.exposure)),
        ("DynamicRange", _DYNAMIC_RANGE_OUT.get(recipe.dynamic_range, "100")),
        ("WideDRange", "0"),
        (
            "FilmSimulation",
            _FILM_SIM_OUT.get(recipe.film_simulation, "Provia"),
        ),
        ("BlackImageTone", "0"),
        ("MonochromaticColor_RG", "0"),
        ("GrainEffect", _GRAIN_OUT.get(recipe.grain, "OFF")),
        ("GrainEffectSize", "SMALL"),
        ("ChromeEffect", _CHROME_OUT.get(recipe.color_chrome, "OFF")),
        ("ColorChromeBlue", None),
        ("SmoothSkinEffect", None),
        ("WBShootCond", "OFF" if recipe.white_balance == "AsShot" else "ON"),
        (
            "WhiteBalance",
            _WHITE_BALANCE_OUT.get(recipe.white_balance, "AsShot"),
        ),
        ("WBShiftR", str(recipe.wb_shift_r)),
        ("WBShiftB", str(recipe.wb_shift_b)),
        ("WBColorTemp", f"{recipe.color_temp}K" if is_temp else "0K"),
        ("HighlightTone", str(recipe.highlights)),
        ("ShadowTone", str(recipe.shadows)),
        ("Color", str(recipe.color)),
        ("Sharpness", str(recipe.sharpness)),
        ("NoisReduction", str(recipe.noise_reduction)),  # sic, see parse_fp.
        ("Clarity", "0"),
        ("LensModulationOpt", "OFF"),
        ("ColorSpace", _COLOR_SPACE.get(recipe.color_space, "sRGB")),
        ("HDR", None),
    ]

    root = ET.Element(
        "ConversionProfile", application="XRFC", version="1.10.0.0"
    )
    group = ET.SubElement(
        root,
        "PropertyGroup",
        device=device,
        version=device_version,
        label=label,
    )
    for tag, value in fields:
        ET.SubElement(group, tag).text = value
    ET.indent(root, space="    ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="utf-8"?>\n{body}\n'
