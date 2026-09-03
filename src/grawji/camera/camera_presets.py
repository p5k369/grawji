"""Transfer grawji recipes into gen5 custom presets over USB.

XProcessor5 bodies expose the C1-C7 custom settings as PTP device
properties (see grawji.preset_recipe), so writing a recipe is a plain
SetDevicePropValue sequence on one connection: select the slot via
0xD18C, write the name and the recipe properties in the X RAW Studio
order, then read everything back to verify. There is no checksum and no
object transfer, unlike the gen3/gen4 settings-blob path in
grawji.camera_backup, which dispatches here when the connected body
advertises the preset-slot property.
"""

from __future__ import annotations

import logging
import struct
import time

from grawji.camera.camera_types import (
    BackupTransferError,
    Camera,
    TransferResult,
)
from grawji.camera.preset_recipe import (
    NUM_SLOTS,
    PASSTHROUGH_DEFAULTS,
    PROP_CLARITY,
    PROP_MONO_MG,
    PROP_MONO_WC,
    PROP_PRESET_NAME,
    PROP_PRESET_SLOT,
    PROP_WB_COLOR_TEMP,
    PROP_WB_SHIFT_B,
    PROP_WB_SHIFT_R,
    PROP_WHITE_BALANCE,
    encode_recipe,
    readback_matches,
    unsupported_fields,
)
from grawji.recipe import Recipe

# Properties some bodies reject (0x201c) that must not abort the whole
# transfer.
_SOFT_REJECT = {
    PROP_CLARITY: "clarity (set it on the camera and resave)",
    PROP_MONO_WC: "monochromatic color toning (set it on the camera)",
    PROP_MONO_MG: "monochromatic color toning (set it on the camera)",
}

_log = logging.getLogger(__name__)

_GET_DEVICE_PROP_VALUE = 0x1015
_SET_DEVICE_PROP_VALUE = 0x1016
_PTP_OK = 0x2001

_SLOT_SWITCH_DELAY_S = 0.1
_NAME_MAX = 25

# Properties read per slot before writing: the passthrough values plus
# the WB cluster an "AsShot" recipe echoes back.
_BASE_PROPS = (
    *PASSTHROUGH_DEFAULTS,
    PROP_WHITE_BALANCE,
    PROP_WB_COLOR_TEMP,
    PROP_WB_SHIFT_R,
    PROP_WB_SHIFT_B,
)

# A property value read may come back as a u32 or a bare u16.
_U32_SIZE = 4
_U16_SIZE = 2


def supports_presets(props: frozenset[int]) -> bool:
    """Whether a body's advertised properties include the preset slot."""
    return PROP_PRESET_SLOT in props


def _get_prop(cam: Camera, prop: int) -> int | None:
    """Read a device property's numeric value, or None if unreadable."""
    code, _p, data = cam.send_command(_GET_DEVICE_PROP_VALUE, [prop])
    if code != _PTP_OK or not data:
        return None
    if len(data) >= _U32_SIZE:
        return int(struct.unpack_from("<I", data)[0]) & 0xFFFF
    if len(data) >= _U16_SIZE:
        return int(struct.unpack_from("<H", data)[0])
    return data[0]


def _set_prop(cam: Camera, prop: int, value: int) -> int:
    """Write a u16 device property."""
    return _set_prop_data(cam, prop, struct.pack("<H", value))


def _set_prop_data(cam: Camera, prop: int, data: bytes) -> int:
    """Write a raw property dataset."""
    code, _p = cam.send_data_command(_SET_DEVICE_PROP_VALUE, [prop], data)
    return code


def _decode_ptp_string(data: bytes) -> str:
    """Decode a PTP string dataset."""
    if not data:
        return ""
    count = data[0]
    raw = data[1 : 1 + count * 2]
    return raw.decode("utf-16-le", "replace").rstrip("\x00")


def _encode_ptp_string(text: str) -> bytes:
    """Encode a PTP string dataset."""
    if not text:
        return b"\x00"
    encoded = (text + "\x00").encode("utf-16-le")
    return bytes([len(encoded) // 2]) + encoded


def _select_slot(cam: Camera, slot: int) -> None:
    """Make bank slot (0-based) the camera's active preset."""
    code = _set_prop(cam, PROP_PRESET_SLOT, slot + 1)
    if code != _PTP_OK:
        raise BackupTransferError(
            f"could not select bank C{slot + 1}: 0x{code:04x}"
        )
    time.sleep(_SLOT_SWITCH_DELAY_S)


def _read_name(cam: Camera) -> str:
    """The active slot's name, or "" if unreadable."""
    code, _p, data = cam.send_command(
        _GET_DEVICE_PROP_VALUE, [PROP_PRESET_NAME]
    )
    if code != _PTP_OK:
        return ""
    return _decode_ptp_string(data).strip()


def _active_slot(cam: Camera) -> int | None:
    """The camera's current active slot (0-based), or None."""
    raw = _get_prop(cam, PROP_PRESET_SLOT)
    if raw is None or not 1 <= raw <= NUM_SLOTS:
        return None
    return raw - 1


def _restore_slot(cam: Camera, slot: int | None) -> None:
    """Put the camera back on the slot the user had active."""
    if slot is None:
        return
    if _set_prop(cam, PROP_PRESET_SLOT, slot + 1) != _PTP_OK:
        _log.debug("presets: could not restore the active slot")


def read_preset_names(cam: Camera) -> list[str]:
    """Return the current names of all bank slots.

    Selecting each slot to read its name changes the camera's active
    preset, so the original selection is restored afterwards.
    """
    original = _active_slot(cam)
    names = []
    try:
        for slot in range(NUM_SLOTS):
            _select_slot(cam, slot)
            names.append(_read_name(cam))
    finally:
        _restore_slot(cam, original)
    return names


def _write_name(cam: Camera, name: str) -> tuple[int, list[str]]:
    """Write the active slot's name; return (applied, problem notes)."""
    code = _set_prop_data(cam, PROP_PRESET_NAME, _encode_ptp_string(name))
    if code != _PTP_OK:
        return 0, [f"name not accepted (0x{code:04x})"]
    stored = _read_name(cam)
    if stored == name:
        return 1, []
    return 0, [f"name stored as {stored!r}"]


def _write_slot(
    cam: Camera, slot: int, recipe: Recipe | None, name: str | None
) -> tuple[int, list[str]]:
    """Write one slot."""
    applied = 0
    notes = []

    if name is not None:
        count, name_notes = _write_name(cam, name)
        applied += count
        notes.extend(name_notes)

    if recipe is None:
        return applied, notes

    base = {}
    for prop in _BASE_PROPS:
        value = _get_prop(cam, prop)
        if value is not None:
            base[prop] = value

    ignored = []
    for prop, value in encode_recipe(recipe, base):
        code = _set_prop(cam, prop, value)
        read_back = _get_prop(cam, prop)
        if read_back is None and code == _PTP_OK:
            # ACKed but unverifiable, trust the ACK rather than fail.
            applied += 1
        elif read_back is not None and readback_matches(
            prop, value, read_back
        ):
            applied += 1
            if code != _PTP_OK:
                _log.debug(
                    "presets: 0x%04x rejected 0x%04x but already holds it",
                    prop,
                    value,
                )
        elif code != _PTP_OK and prop in _SOFT_REJECT:
            note = _SOFT_REJECT[prop]
            if note not in notes:
                notes.append(note)
        elif code != _PTP_OK:
            raise BackupTransferError(
                f"camera rejected property 0x{prop:04x}=0x{value:04x} "
                f"on bank C{slot + 1} (0x{code:04x})"
            )
        else:
            ignored.append(prop)

    if ignored:
        preview = ", ".join(f"0x{p:04x}" for p in ignored[:8])
        raise BackupTransferError(
            f"camera ACKed but silently ignored {len(ignored)} propert"
            f"{'y' if len(ignored) == 1 else 'ies'} on bank C{slot + 1} "
            f"({preview}); settings not fully written"
        )
    return applied, notes


def transfer_presets(
    cam: Camera,
    assignments: dict[int, Recipe],
    *,
    names: dict[int, str] | None = None,
    model: str | None = None,
) -> TransferResult:
    """Write recipes (and optional names) into the camera's presets.

    Args:
        cam: A connected camera whose body advertises the preset
            properties (see supports_presets).
        assignments: Bank index (0-based) -> recipe to store there.
        names: Optional bank index -> new name (truncated to fit).
        model: The body model, for the result.

    Returns:
        A TransferResult; maintained is unused on this path and dropped
        carries both unsupported recipe features and camera-side notes.

    Raises:
        BackupTransferError: On a PTP error or a silently ignored write.
    """
    names = names or {}
    if not assignments and not names:
        raise BackupTransferError("no bank assignments to write")

    slots = sorted(set(assignments) | set(names))
    bad = [s for s in slots if not 0 <= s < NUM_SLOTS]
    if bad:
        raise BackupTransferError(f"bank slot out of range: {bad}")

    dropped: dict[int, list[str]] = {}
    applied = 0
    original = _active_slot(cam)
    try:
        for slot in slots:
            _select_slot(cam, slot)
            recipe = assignments.get(slot)
            name = names.get(slot)
            if name is not None:
                name = name[:_NAME_MAX]
            notes = unsupported_fields(recipe) if recipe else []
            count, slot_notes = _write_slot(cam, slot, recipe, name)
            applied += count
            notes.extend(slot_notes)
            if notes:
                dropped[slot] = notes
    finally:
        _restore_slot(cam, original)

    _log.debug(
        "presets: wrote %d slots, %d properties confirmed", len(slots), applied
    )
    return TransferResult(
        model=model or "",
        slots=tuple(slots),
        applied=applied,
        maintained=(),
        dropped=dropped,
    )


__all__ = [
    "read_preset_names",
    "supports_presets",
    "transfer_presets",
]
