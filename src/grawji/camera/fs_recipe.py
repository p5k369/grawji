"""Write a Recipe into a body's FS1-FSn film-sim dial presets.

Some bodies (the X-E5 is the first mapped one) have a film simulation
dial whose FS positions store a full per-position recipe. Those
positions are not exposed through the gen5 preset properties (the slot
selector 0xD18C rejects anything past C7), so they are written through
the settings-blob restore path instead.

Unlike the C1-C7 bank records of backup_recipe, the FS recipe is stored
as parallel per-parameter arrays: for each parameter the FS1..FSn
values are adjacent elements, so a field is addressed as its FS1 offset
plus the slot index (times the element size).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from grawji.camera.backup_recipe import BackupWriteError
from grawji.recipe import Recipe

# The X-E5 whole-file checksum: additive u16 little-endian at 0x120.
# A patch inside the covered payload delta-updates the stored value
# (hardware-verified on every controlled diff round: the stored u16
# moved by exactly the content byte delta).
_CHECKSUM_OFFSET = 0x120

# X-E5 FS-dial film-sim codes, all hardware verified.
_FS_FILM_SIMS = {
    "Provia": 0x01,
    "Astia": 0x02,
    "Velvia": 0x04,
    "Sepia": 0x06,
    "Monochrome": 0x09,
    "MonochromeR": 0x0A,
    "MonochromeYe": 0x0B,
    "MonochromeG": 0x0C,
    "ProNegStd": 0x0D,
    "ProNegHi": 0x0E,
    "ClassicChrome": 0x0F,
    "ClassicNeg": 0x10,
    "NostalgicNeg": 0x11,
    "RealaAce": 0x12,
    "Eterna": 0x13,
    "EternaBleach": 0x14,
    "Acros": 0x16,
    "AcrosR": 0x17,
    "AcrosYe": 0x18,
    "AcrosG": 0x19,
}

# WB-mode codes of the blob preamble
_WB_CODES = {
    "Auto": 0x00,
    "Daylight": 0x03,
    "Shade": 0x04,
    "Fluorescent1": 0x05,
    "Fluorescent2": 0x06,
    "Fluorescent3": 0x07,
    "Incandescent": 0x08,
    "Underwater": 0x09,
    "Temperature": 0x0A,
}

# DR codes: Auto=0, DR100=1, DR200=2 hardware-measured on the X-E5
_DR_CODES = {"Auto": 0, "DR100": 1, "DR200": 2, "DR400": 3}

_CHROME_CODES = {"Off": 0, "Weak": 1, "Strong": 2}
_GRAIN_ROUGH_CODES = {"Strong": 0, "Weak": 1, "Off": 2}
_GRAIN_SIZE_CODES = {"Small": 0, "Large": 1}

# Monochromatic Color toning (B&W sims only). Encoded raw = 18 - value,
# neutral 18, like the other descending FS-array codes. Hardware-verified
# on the X-E5
_MONO_TONING_PREFIXES = ("Acros", "Monochrome")
_MONO_NEUTRAL = 18
_MONO_LIMIT = 18

# A u16 kelvin field occupies two blob bytes, everything else is a byte.
_U16_SIZE = 2
_BYTE_MAX = 0xFF


def _mono_code(value: int) -> int:
    """Encode a mono toning axis: raw = 18 - value, clamped to +-18."""
    clamped = max(-_MONO_LIMIT, min(_MONO_LIMIT, value))
    return _MONO_NEUTRAL - clamped


@dataclass(frozen=True)
class FsField:
    """One parameter array: FS1's offset and the per-slot element size."""

    offset: int
    step: int = 1


@dataclass(frozen=True)
class FsLayout:
    """Where one body stores its FS dial-preset parameter arrays.

    Attributes:
        blob_size: Expected total blob length, a sanity guard.
        num_slots: Number of FS dial positions.
        fields: Parameter name -> its FS1 offset and slot stride.
        volatile_offsets: Blob offsets the camera rewrites itself on a
            restore (the checksum field).
    """

    blob_size: int
    num_slots: int
    fields: dict[str, FsField]
    volatile_offsets: frozenset[int] = frozenset(
        {_CHECKSUM_OFFSET, _CHECKSUM_OFFSET + 1}
    )


_XE5 = FsLayout(
    blob_size=70524,
    num_slots=3,
    fields={
        "film_sim": FsField(1991, step=3),
        "wb_kelvin": FsField(34704, step=2),
        "wb_mode": FsField(34716),
        "nr": FsField(34722),
        "clarity": FsField(34728),
        "mono_wc": FsField(34731),
        "mono_mg": FsField(34737),
        "dr": FsField(34743),
        "color": FsField(34752),
        "sharpness": FsField(34758),
        "highlight": FsField(34764),
        "shadow": FsField(34770),
        "color_chrome": FsField(34776),
        "color_chrome_blue": FsField(34779),
        "grain_rough": FsField(34782),
        "grain_size": FsField(34785),
        "smooth_skin": FsField(34788),
        "wb_shift_r": FsField(34864),
        "wb_shift_b": FsField(34870),
    },
)

# EXIF/DeviceInfo model (normalized like backup_recipe) -> FS layout.
LAYOUTS: dict[str, FsLayout] = {
    "XE5": _XE5,
}


def layout_for(model: str | None) -> FsLayout | None:
    """Return the FS dial layout for a body, or None if unmapped."""
    if model is None:
        return None
    normalized = "".join(
        ch for ch in model.upper().replace("FUJIFILM", "") if ch.isalnum()
    )
    return LAYOUTS.get(normalized)


def _tone_code(value: float) -> int:
    """Encode highlight/shadow: raw = (value + 2) * 2, half steps."""
    return round((value + 2) * 2)


def _encode(
    layout: FsLayout, slot: int, recipe: Recipe
) -> tuple[dict[int, tuple[int, int]], list[str]]:
    """Map a recipe to {offset: (value, size)} plus dropped fields."""

    def at(name: str) -> FsField:
        return layout.fields[name]

    def offset(name: str) -> int:
        field = at(name)
        return field.offset + slot * field.step

    out: dict[int, tuple[int, int]] = {}
    dropped: list[str] = []

    if recipe.film_simulation in _FS_FILM_SIMS:
        out[offset("film_sim")] = (_FS_FILM_SIMS[recipe.film_simulation], 1)
    else:
        dropped.append(
            f"film simulation {recipe.film_simulation} "
            "(not yet verified on this body's FS dial)"
        )

    if recipe.dynamic_range in _DR_CODES:
        out[offset("dr")] = (_DR_CODES[recipe.dynamic_range], 1)
    else:
        dropped.append(f"dynamic range {recipe.dynamic_range}")

    out[offset("nr")] = (recipe.noise_reduction + 4, 1)
    out[offset("clarity")] = (recipe.clarity + 6, 1)
    out[offset("color")] = (7 - recipe.color, 1)
    out[offset("sharpness")] = (4 - recipe.sharpness, 1)
    out[offset("highlight")] = (_tone_code(recipe.highlights), 1)
    out[offset("shadow")] = (_tone_code(recipe.shadows), 1)
    out[offset("color_chrome")] = (_CHROME_CODES[recipe.color_chrome], 1)
    out[offset("color_chrome_blue")] = (
        _CHROME_CODES[recipe.color_chrome_blue],
        1,
    )
    out[offset("grain_rough")] = (_GRAIN_ROUGH_CODES[recipe.grain], 1)
    if recipe.grain != "Off":
        out[offset("grain_size")] = (
            _GRAIN_SIZE_CODES[recipe.grain_size],
            1,
        )
    out[offset("smooth_skin")] = (_CHROME_CODES[recipe.smooth_skin], 1)

    # White balance: "AsShot" leaves the slot's stored WB untouched.
    if recipe.white_balance != "AsShot":
        if recipe.white_balance in _WB_CODES:
            out[offset("wb_mode")] = (_WB_CODES[recipe.white_balance], 1)
            out[offset("wb_shift_r")] = (9 - recipe.wb_shift_r, 1)
            out[offset("wb_shift_b")] = (9 - recipe.wb_shift_b, 1)
            if recipe.white_balance == "Temperature":
                out[offset("wb_kelvin")] = (recipe.color_temp, 2)
        else:
            dropped.append(f"white balance {recipe.white_balance}")

    is_mono = recipe.film_simulation.startswith(_MONO_TONING_PREFIXES)
    if is_mono and "mono_wc" in layout.fields:
        out[offset("mono_wc")] = (_mono_code(recipe.mono_warm_cool), 1)
        out[offset("mono_mg")] = (_mono_code(recipe.mono_magenta_green), 1)
    elif is_mono and (recipe.mono_warm_cool or recipe.mono_magenta_green):
        dropped.append("monochromatic color toning")

    if recipe.exposure:
        dropped.append("exposure compensation")

    return out, dropped


def unsupported_fields(layout: FsLayout, recipe: Recipe) -> list[str]:
    """Recipe features an FS dial position cannot store."""
    return _encode(layout, 0, recipe)[1]


def write_fs_recipe(
    blob: bytes, layout: FsLayout, slot: int, recipe: Recipe
) -> bytes:
    """Return a copy of blob with recipe written into FS slot.

    Args:
        blob: A settings-backup blob read from the camera.
        layout: The connected body's FS layout (from layout_for).
        slot: Zero-based dial position, 0..num_slots-1 (FS1..FSn).
        recipe: The recipe to store there.

    Raises:
        BackupWriteError: On a bad slot or a blob-size mismatch.
    """
    if not 0 <= slot < layout.num_slots:
        raise BackupWriteError(
            f"FS slot {slot} out of range 0..{layout.num_slots - 1}"
        )
    if len(blob) != layout.blob_size:
        raise BackupWriteError(
            f"blob is {len(blob)} bytes, expected {layout.blob_size} for "
            "this body"
        )
    encoded, _dropped = _encode(layout, slot, recipe)
    out = bytearray(blob)
    delta = 0
    for off, (value, size) in encoded.items():
        if size == _U16_SIZE:
            old = out[off] + out[off + 1]
            struct.pack_into("<H", out, off, value)
            delta += out[off] + out[off + 1] - old
        else:
            if not 0 <= value <= _BYTE_MAX:
                raise BackupWriteError(
                    f"value {value} out of byte range at offset {off}"
                )
            delta += value - out[off]
            out[off] = value
    stored = struct.unpack_from("<H", out, _CHECKSUM_OFFSET)[0]
    struct.pack_into("<H", out, _CHECKSUM_OFFSET, (stored + delta) & 0xFFFF)
    return bytes(out)


def write_fs_recipes(
    blob: bytes, layout: FsLayout, assignments: dict[int, Recipe]
) -> bytes:
    """Write several FS slots at once (slot index -> recipe)."""
    for slot, recipe in assignments.items():
        blob = write_fs_recipe(blob, layout, slot, recipe)
    return blob
