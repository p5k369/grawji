"""Encode a Recipe as gen5 custom-preset PTP property writes.

XProcessor5 bodies expose the C1-C7 custom settings as plain PTP device
properties in USB RAW CONV./BACKUP RESTORE mode: 0xD18C selects the active
slot, 0xD18D is the slot name, and 0xD18E-0xD1A5 are the slot's recipe values.
Saving a preset is a bare sequence of SetDevicePropValue writes with no commit
opcode.
"""

from __future__ import annotations

import rawji
from rawji.fuji_enums import (
    ChromeEffect,
    GrainEffect,
    GrainEffectSize,
    grain_effect_code,
)
from rawji.fuji_profile import encode_noise_reduction

from grawji.backup_recipe import BackupWriteError
from grawji.recipe import Recipe

# Slot selector and slot name (not part of the recipe write list).
PROP_PRESET_SLOT = 0xD18C
PROP_PRESET_NAME = 0xD18D

PROP_IMAGE_SIZE = 0xD18E
PROP_IMAGE_QUALITY = 0xD18F
PROP_DYNAMIC_RANGE = 0xD190
PROP_UNKNOWN_D191 = 0xD191
PROP_FILM_SIMULATION = 0xD192
PROP_MONO_WC = 0xD193
PROP_MONO_MG = 0xD194
PROP_GRAIN = 0xD195
PROP_COLOR_CHROME = 0xD196
PROP_COLOR_CHROME_BLUE = 0xD197
PROP_SMOOTH_SKIN = 0xD198
PROP_WHITE_BALANCE = 0xD199
PROP_WB_SHIFT_R = 0xD19A
PROP_WB_SHIFT_B = 0xD19B
PROP_WB_COLOR_TEMP = 0xD19C
PROP_HIGHLIGHT = 0xD19D
PROP_SHADOW = 0xD19E
PROP_COLOR = 0xD19F
PROP_SHARPNESS = 0xD1A0
PROP_NOISE_REDUCTION = 0xD1A1
PROP_CLARITY = 0xD1A2
PROP_LONG_EXPOSURE_NR = 0xD1A3
PROP_COLOR_SPACE = 0xD1A4
PROP_UNKNOWN_D1A5 = 0xD1A5

# Number of C1..Cn banks a gen5 body exposes over these properties.
NUM_SLOTS = 7

# Properties whose meaning is unknown or not a recipe field (image size,
# image quality, long-exposure NR, colour space, two unknowns).
PASSTHROUGH_DEFAULTS: dict[int, int] = {
    PROP_IMAGE_SIZE: 7,  # L 3:2
    PROP_IMAGE_QUALITY: 4,
    PROP_UNKNOWN_D191: 0,
    PROP_LONG_EXPOSURE_NR: 1,  # on
    PROP_COLOR_SPACE: 1,  # sRGB
    PROP_UNKNOWN_D1A5: 7,
}

# The tone/NR reads on some bodies return 0x8000 for a value the slot
# never stored
UNSET_SENTINEL = 0x8000

# Grain Off writes as code 1, but gen5 bodies store an Off code that
# keeps the grain-size half: the X-E5's factory slots hold 6 (Off after
# Small) and an Off written over a Large-grain preset reads back 7
# All decode as Off.
_GRAIN_OFF_WRITE = 1
_GRAIN_OFF_STORED = frozenset({6, 7})

_DR_PERCENT = {"DR100": 100, "DR200": 200, "DR400": 400}

# Only the Acros/Monochrome families take the mono toning values. Those
# plus Sepia lock the colour axis (the camera greys it out), so the
# colour property is omitted for them, as X Raw Studio does.
_MONO_TONING_PREFIXES = ("Acros", "Monochrome")


def _u16(value: int) -> int:
    """Two's-complement a small signed value into a u16 wire value."""
    return value & 0xFFFF


def _x10(value: float) -> int:
    """Encode a tone-style value as the value*10 i16 wire value."""
    return _u16(round(value * 10))


def _film_sim_code(name: str) -> int:
    """The preset film-sim code (identical to the d185/rawji enum)."""
    try:
        return int(rawji.FilmSimulation[name])
    except KeyError:
        raise BackupWriteError(f"unknown film simulation {name!r}") from None


def _chrome_code(value: str, label: str) -> int:
    """Encode an Off/Weak/Strong strength (1/2/3, the ChromeEffect enum)."""
    try:
        return int(ChromeEffect[value])
    except KeyError:
        raise BackupWriteError(f"unknown {label} {value!r}") from None


def unsupported_fields(recipe: Recipe) -> list[str]:
    """Recipe features a gen5 preset cannot store (dropped on write)."""
    dropped = []
    if recipe.exposure:
        dropped.append("exposure compensation")
    return dropped


def encode_recipe(
    recipe: Recipe, base: dict[int, int] | None = None
) -> list[tuple[int, int]]:
    """Return the ordered (property, u16 value) write list for a recipe.

    Args:
        recipe: The recipe to encode.
        base: The slot's current property values, echoed back for the
            passthrough properties and for an "AsShot" white balance
            (which leaves the slot's stored WB untouched). Properties
            missing from base fall back to PASSTHROUGH_DEFAULTS, or are
            omitted for the WB cluster.

    Raises:
        BackupWriteError: If an enum-valued field has no known code.
    """
    base = base or {}
    sim = recipe.film_simulation
    is_mono_toning = sim.startswith(_MONO_TONING_PREFIXES)

    def passthrough(prop: int) -> tuple[int, int]:
        return prop, base.get(prop, PASSTHROUGH_DEFAULTS[prop])

    try:
        dr = _DR_PERCENT[recipe.dynamic_range]
    except KeyError:
        raise BackupWriteError(
            f"unknown dynamic range {recipe.dynamic_range!r}"
        ) from None

    props = [
        passthrough(PROP_IMAGE_SIZE),
        passthrough(PROP_IMAGE_QUALITY),
        (PROP_DYNAMIC_RANGE, dr),
        passthrough(PROP_UNKNOWN_D191),
        (PROP_FILM_SIMULATION, _film_sim_code(sim)),
    ]

    if is_mono_toning and recipe.mono_warm_cool:
        props.append((PROP_MONO_WC, _x10(recipe.mono_warm_cool)))
    if is_mono_toning and recipe.mono_magenta_green:
        props.append((PROP_MONO_MG, _x10(recipe.mono_magenta_green)))

    grain = grain_effect_code(
        GrainEffect[recipe.grain], GrainEffectSize[recipe.grain_size]
    )
    props.append((PROP_GRAIN, grain))
    props.append(
        (PROP_COLOR_CHROME, _chrome_code(recipe.color_chrome, "color chrome"))
    )
    props.append(
        (
            PROP_COLOR_CHROME_BLUE,
            _chrome_code(recipe.color_chrome_blue, "color chrome blue"),
        )
    )
    props.append(
        (PROP_SMOOTH_SKIN, _chrome_code(recipe.smooth_skin, "smooth skin"))
    )

    props.extend(_wb_props(recipe, base))

    props.append((PROP_HIGHLIGHT, _x10(recipe.highlights)))
    props.append((PROP_SHADOW, _x10(recipe.shadows)))
    if not is_mono_toning and sim != "Sepia":
        props.append((PROP_COLOR, _x10(recipe.color)))
    props.append((PROP_SHARPNESS, _x10(recipe.sharpness)))
    props.append(
        (PROP_NOISE_REDUCTION, encode_noise_reduction(recipe.noise_reduction))
    )
    props.append((PROP_CLARITY, _x10(recipe.clarity)))

    props.append(passthrough(PROP_LONG_EXPOSURE_NR))
    props.append(passthrough(PROP_COLOR_SPACE))
    props.append(passthrough(PROP_UNKNOWN_D1A5))
    return props


def _wb_props(recipe: Recipe, base: dict[int, int]) -> list[tuple[int, int]]:
    """The WB cluster: mode, then temperature, then the two shifts.

    "AsShot" echoes the slot's current values so the stored WB survives
    the full-sequence write; if the slot was not readable the cluster is
    omitted and the stored values stand.
    """
    if recipe.white_balance == "AsShot":
        if PROP_WHITE_BALANCE not in base:
            return []
        mode = base[PROP_WHITE_BALANCE]
        props = [(PROP_WHITE_BALANCE, mode)]
        if mode == int(rawji.WhiteBalance.Temperature):
            kelvin = base.get(PROP_WB_COLOR_TEMP)
            if kelvin:
                props.append((PROP_WB_COLOR_TEMP, kelvin))
        props.extend(
            (prop, base[prop])
            for prop in (PROP_WB_SHIFT_R, PROP_WB_SHIFT_B)
            if prop in base
        )
        return props

    try:
        mode = int(rawji.WhiteBalance[recipe.white_balance])
    except KeyError:
        raise BackupWriteError(
            f"unknown white balance {recipe.white_balance!r}"
        ) from None
    props = [(PROP_WHITE_BALANCE, mode)]
    if recipe.white_balance == "Temperature":
        props.append((PROP_WB_COLOR_TEMP, recipe.color_temp))
    props.append((PROP_WB_SHIFT_R, _u16(recipe.wb_shift_r)))
    props.append((PROP_WB_SHIFT_B, _u16(recipe.wb_shift_b)))
    return props


def readback_matches(prop: int, wrote: int, read: int) -> bool:
    """Whether a read-back value confirms a written one."""
    if read == wrote:
        return True
    if (
        prop == PROP_GRAIN
        and wrote == _GRAIN_OFF_WRITE
        and read in _GRAIN_OFF_STORED
    ):
        return True
    sentinel_props = (
        PROP_MONO_WC,
        PROP_MONO_MG,
        PROP_HIGHLIGHT,
        PROP_SHADOW,
        PROP_COLOR,
        PROP_SHARPNESS,
        PROP_NOISE_REDUCTION,
        PROP_CLARITY,
    )
    return prop in sentinel_props and read == UNSET_SENTINEL
