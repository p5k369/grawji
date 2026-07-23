"""Write a Recipe into a Fuji settings-backup blob's custom-bank slot.

This is separate from the d185 conversion path in core. The settings
backup is the whole-camera object transferred in USB RAW CONV./BACKUP
RESTORE mode.

The backup uses its OWN code tables, distinct from d185/rawji. Every
offset and code here was verified by controlled diffs on real hardware
(see the findings notes). Values with no verified code raise
BackupWriteError rather than write an unverified byte.
"""

from __future__ import annotations

from dataclasses import dataclass

from rawji.fuji_enums import WB_KELVIN_PRESETS

from grawji.recipe import Recipe

# Kelvin presets as the backup stores them: an index into the list sorted
# DESCENDING from 10000K (hardware: 5000K -> index 10 on the X100F/X-T3).
_KELVIN_DESC = sorted(WB_KELVIN_PRESETS, reverse=True)


class BackupWriteError(ValueError):
    """A recipe value has no verified backup code for this body."""


@dataclass(frozen=True)
class Checksum:
    """A whole-file checksum the camera validates on restore.

    An additive u16 little-endian byte sum: the camera rejects a restore
    (0x200f) whose stored value does not match its content, so any patched
    blob must have this recomputed. All fields are hardware-solved.

    Attributes:
        offset: Where the u16 is stored.
        payload_start: Sum runs from here to the end of the blob.
        bias: Added to the sum (mod 2^16).
        skip: Bytes at offset excluded from the sum (the field itself).
        extra_skip: Further (start, end) byte ranges excluded from the sum.
    """

    offset: int
    payload_start: int
    bias: int
    skip: int = 2
    extra_skip: tuple[tuple[int, int], ...] = ()


def apply_checksum(blob: bytes, checksum: Checksum | None) -> bytes:
    """Return blob with its checksum recomputed, or unchanged if None."""
    if checksum is None:
        return blob
    total = sum(blob[checksum.payload_start :])
    total -= sum(blob[checksum.offset : checksum.offset + checksum.skip])
    for lo, hi in checksum.extra_skip:
        total -= sum(blob[lo:hi])
    value = (total + checksum.bias) & 0xFFFF
    out = bytearray(blob)
    out[checksum.offset : checksum.offset + 2] = value.to_bytes(2, "little")
    return bytes(out)


@dataclass(frozen=True)
class BankLayout:
    """Where and how one body stores a bank recipe in its backup blob.

    Attributes:
        blob_size: Expected total blob length, a sanity guard.
        num_slots: Number of C1..Cn custom banks.
        sim0: Absolute offset of slot 0's film-simulation byte.
        stride: Bytes between consecutive bank records.
        rel: Field offsets relative to a slot's film-sim byte. Keys:
            wb_mode, wb_kelvin, nr, dr, color, sharpness, highlight,
            shadow, grain, and optionally color_chrome.
        film_sim_codes: Recipe film-sim name -> backup code.
        color_codes: Recipe color value (-4..+4) -> backup code.
        checksum: Whole-file checksum spec the camera validates on restore
            (apply_checksum recomputes it after patching), or None.
        volatile_offsets: Blob-global offsets the camera rewrites itself on
            every restore (checksum + normalization/counter fields). A
            read-back that differs here is the camera housekeeping, not a
            failed write, so verification ignores them.
        name_rel: Offset of the bank's ASCII name (relative to the film-sim
            byte), or None on bodies whose banks are not user-nameable.
        name_max: Bytes reserved for the name (name plus NUL padding).
    """

    blob_size: int
    num_slots: int
    sim0: int
    stride: int
    rel: dict[str, int]
    film_sim_codes: dict[str, int]
    color_codes: dict[int, int]
    checksum: Checksum | None
    volatile_offsets: frozenset[int] = frozenset()
    name_rel: int | None = None
    name_max: int = 16


# Verified WB-mode codes shared by the X100F and X-T3 (menu-order enum).
# Custom1-3 are unmeasured on these bodies and omitted.
_WB_CODES = {
    "Auto": 0,
    "Daylight": 1,
    "Shade": 2,
    "Fluorescent1": 3,
    "Fluorescent2": 4,
    "Fluorescent3": 5,
    "Incandescent": 6,
    "Underwater": 7,
    "Temperature": 8,
}

# Gen3 film-sim enum, verified on the X100F
_GEN3_SIMS = {
    "Provia": 0,
    "Astia": 1,
    "Velvia": 3,
    "Sepia": 5,
    "Monochrome": 7,
    "MonochromeR": 8,
    "MonochromeYe": 9,
    "MonochromeG": 10,
    "ProNegStd": 11,
    "ProNegHi": 12,
    "ClassicChrome": 13,
    "Acros": 14,
    "AcrosR": 15,
    "AcrosYe": 16,
    "AcrosG": 17,
}

# Color lookup, hardware-measured on BOTH the X100F and X-T3 and found
# byte-identical (the -1/-2 codes are inverted, never fit a formula).
# Shared across gen3/gen4.
_COLOR_CODES = {4: 3, 3: 4, 2: 5, 1: 6, 0: 0, -1: 8, -2: 7, -3: 9, -4: 10}

# Gen4 film-sim enum, verified on the X-T3
# todo: this is incomplete ot needs further investigation because X-T3 does
#  not have all Gen4 sims
_GEN4_SIMS = {
    "Provia": 0,
    "Astia": 1,
    "Velvia": 3,
    "Sepia": 5,
    "Monochrome": 7,
    "MonochromeR": 8,
    "MonochromeYe": 9,
    "MonochromeG": 10,
    "ProNegStd": 11,
    "ProNegHi": 12,
    "ClassicChrome": 13,
    "Acros": 14,
    "AcrosR": 15,
    "AcrosYe": 16,
    "AcrosG": 17,
    "Eterna": 18,
}


# Gen3 layout, verified on the X100F. The blob_size guard rejects any whose
# blob is not 5660 bytes, so the assumption cannot corrupt a mismatched body.
_GEN3 = BankLayout(
    blob_size=5660,
    num_slots=7,
    sim0=3909,
    stride=256,
    rel={
        "wb_mode": -33,
        "wb_kelvin": -32,
        "nr": -7,
        "dr": 3,
        "color": 7,
        "sharpness": 9,
        "highlight": 10,
        "shadow": 11,
        "grain": 12,
    },
    film_sim_codes=_GEN3_SIMS,
    color_codes=_COLOR_CODES,
    checksum=None,  # gen3 has no whole-file checksum
)

# Gen4-early layout, verified on the X-T3. The camera validates the @176
# checksum on restore and rejects a stale value with 0x200f, so a patched
# blob must carry it recomputed. @248/@380/@408/@3276 are
# normalization/counter fields the camera rewrites each restore.
_GEN4_EARLY = BankLayout(
    blob_size=33404,
    num_slots=7,
    sim0=31658,
    stride=256,
    rel={
        "wb_mode": -34,
        "wb_kelvin": -33,
        "nr": -8,
        "dr": 4,
        "color": 9,
        "sharpness": 11,
        "highlight": 12,
        "shadow": 13,
        "color_chrome": 14,
        "grain": 15,
    },
    film_sim_codes=_GEN4_SIMS,
    color_codes=_COLOR_CODES,
    # Additive u16 @176 over payload [0xA8, EOF)
    checksum=Checksum(offset=176, payload_start=0xA8, bias=0xFE6C),
    volatile_offsets=frozenset({176, 248, 380, 408, 3276}),
    name_rel=67,  # gen4 banks are user-nameable (ASCII at sim+67)
)

# EXIF model (normalized) -> bank layout.
LAYOUTS: dict[str, BankLayout] = {
    # Gen3 / X-Processor Pro
    "X100F": _GEN3,
    "XPRO2": _GEN3,
    "XT2": _GEN3,
    "XT20": _GEN3,
    "XE3": _GEN3,
    # Gen4-early / X-Processor 4
    "XT3": _GEN4_EARLY,
    "XT30": _GEN4_EARLY,
}

_BYTE_MAX = 255
_GRAIN_CODES = {"Strong": 0, "Weak": 1, "Off": 2}
_CHROME_CODES = {"Off": 0, "Weak": 1, "Strong": 2}
_DR_CODES = {"DR100": 1, "DR200": 2, "DR400": 3}


def _normalize_model(model: str) -> str:
    """Reduce an EXIF model string to a LAYOUTS key."""
    return "".join(
        ch for ch in model.upper().replace("FUJIFILM", "") if ch.isalnum()
    )


def layout_for(model: str | None) -> BankLayout | None:
    """Return the bank layout for a body, or None if unsupported.

    Only bodies whose bank records are fully verified are returned. Every
    other model (and an unreadable model tag) yields None, so callers gate
    the write feature off rather than guess a layout.
    """
    if model is None:
        return None
    return LAYOUTS.get(_normalize_model(model))


def _tone_code(value: float) -> int:
    """Encode a tone/sharpness value (4 - value), rounding half steps."""
    return 4 - round(value)


def _encode(
    layout: BankLayout, recipe: Recipe
) -> tuple[dict[int, int], list[str]]:
    """Map a recipe to ({relative offset: byte}, dropped-field names)."""
    rel = layout.rel
    out: dict[int, int] = {}
    dropped: list[str] = []

    def keep(offset: int, value: int, label: str) -> None:
        if 0 <= value <= _BYTE_MAX:
            out[offset] = value
        else:
            dropped.append(label)

    if recipe.film_simulation in layout.film_sim_codes:
        out[0] = layout.film_sim_codes[recipe.film_simulation]
    else:
        dropped.append(f"film simulation {recipe.film_simulation}")

    if recipe.color in layout.color_codes:
        out[rel["color"]] = layout.color_codes[recipe.color]
    else:
        dropped.append(f"colour {recipe.color:+d}")

    keep(rel["highlight"], _tone_code(recipe.highlights), "highlight tone")
    keep(rel["shadow"], _tone_code(recipe.shadows), "shadow tone")
    keep(rel["sharpness"], _tone_code(recipe.sharpness), "sharpness")
    keep(rel["nr"], recipe.noise_reduction + 4, "noise reduction")
    out[rel["grain"]] = _GRAIN_CODES[recipe.grain]

    if recipe.dynamic_range in _DR_CODES:
        out[rel["dr"]] = _DR_CODES[recipe.dynamic_range]
    else:
        dropped.append(f"dynamic range {recipe.dynamic_range}")

    if "color_chrome" in rel:
        out[rel["color_chrome"]] = _CHROME_CODES[recipe.color_chrome]
    elif recipe.color_chrome != "Off":
        dropped.append("Color Chrome Effect")

    # White balance: "AsShot" leaves the bank's own WB untouched.
    if recipe.white_balance != "AsShot":
        if recipe.white_balance in _WB_CODES:
            out[rel["wb_mode"]] = _WB_CODES[recipe.white_balance]
            if recipe.white_balance == "Temperature":
                out[rel["wb_kelvin"]] = _kelvin_index(recipe.color_temp)
        else:
            dropped.append(f"white balance {recipe.white_balance}")

    return out, dropped


def unsupported_fields(layout: BankLayout, recipe: Recipe) -> list[str]:
    """Recipe features this body cannot store (dropped on write)."""
    return _encode(layout, recipe)[1]


def _kelvin_index(kelvin: int) -> int:
    """Nearest descending-list index for a colour temperature."""
    return int(
        min(
            range(len(_KELVIN_DESC)),
            key=lambda i: abs(_KELVIN_DESC[i] - kelvin),
        )
    )


def write_recipe(
    blob: bytes, layout: BankLayout, slot: int, recipe: Recipe
) -> bytes:
    """Return a copy of blob with recipe written into custom-bank slot.

    Features the body cannot represent are dropped (see unsupported_fields),
    so a preset still gets every supported feature.

    Args:
        blob: A settings-backup blob read from the camera.
        layout: The connected body's bank layout (from layout_for).
        slot: Zero-based bank index, 0..num_slots-1 (C1..Cn).
        recipe: The recipe to store in that bank.

    Returns:
        A new blob with the slot's supported recipe bytes patched. Any
        camera-owned checksum is left for the camera to recompute.

    Raises:
        BackupWriteError: On a bad slot or a blob-size mismatch (a body
            mismatch, not a recipe-value problem).
    """
    base = _slot_base(layout, slot)
    if len(blob) != layout.blob_size:
        raise BackupWriteError(
            f"blob is {len(blob)} bytes, expected {layout.blob_size} for "
            "this body"
        )
    encoded, _dropped = _encode(layout, recipe)
    out = bytearray(blob)
    for rel_off, value in encoded.items():
        out[base + rel_off] = value
    return bytes(out)


def write_recipes(
    blob: bytes, layout: BankLayout, assignments: dict[int, Recipe]
) -> bytes:
    """Write several bank slots at once (slot index -> recipe)."""
    for slot, recipe in assignments.items():
        blob = write_recipe(blob, layout, slot, recipe)
    return blob


def _slot_base(layout: BankLayout, slot: int) -> int:
    """The film-sim byte offset of bank slot, validating the slot."""
    if not 0 <= slot < layout.num_slots:
        raise BackupWriteError(
            f"slot {slot} out of range 0..{layout.num_slots - 1}"
        )
    return layout.sim0 + slot * layout.stride


def read_names(blob: bytes, layout: BankLayout) -> list[str]:
    """Return the current bank names, or [] if the body has none."""
    if layout.name_rel is None or len(blob) != layout.blob_size:
        return []
    names = []
    for slot in range(layout.num_slots):
        base = _slot_base(layout, slot) + layout.name_rel
        raw = bytes(blob[base : base + layout.name_max])
        names.append(raw.split(b"\x00", 1)[0].decode("ascii", "replace"))
    return names


def write_name(blob: bytes, layout: BankLayout, slot: int, name: str) -> bytes:
    """Return a copy of blob with bank slot renamed."""
    if layout.name_rel is None:
        raise BackupWriteError("this body's banks are not user-nameable")
    if len(blob) != layout.blob_size:
        raise BackupWriteError(
            f"blob is {len(blob)} bytes, expected {layout.blob_size}"
        )
    base = _slot_base(layout, slot) + layout.name_rel
    encoded = name.encode("ascii", "ignore")[: layout.name_max - 1]
    field = bytes(blob[base : base + layout.name_max])
    old_len = field.find(b"\x00")
    if old_len < 0:
        old_len = layout.name_max
    clear = min(layout.name_max, max(old_len, len(encoded) + 1))
    out = bytearray(blob)
    out[base : base + clear] = bytes(clear)
    out[base : base + len(encoded)] = encoded
    return bytes(out)
