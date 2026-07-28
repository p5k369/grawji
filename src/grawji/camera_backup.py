"""Transfer grawji recipes into a camera's custom banks over USB.

The camera must be in USB RAW CONV./BACKUP RESTORE mode. The whole-camera
settings object (PTP handle 0) is downloaded, the requested bank slots are
patched with backup_recipe, and the object is restored. A read-back then
confirms every intended byte took, distinguishing the camera's own
housekeeping fields (checksum, normalization counters) from a silent
no-op.

Only bodies with a verified bank layout are supported.
The layout is chosen from the model string in the blob header,
which is authoritative because the blob came from the connected body. The
blob_size guard in backup_recipe rejects a relative whose blob is not the
expected size before anything is written.

Gen5 bodies take a different route: they expose the C1-C7
presets as plain PTP device properties, so when the connected body
advertises the preset-slot property 0xD18C the transfer dispatches to
grawji.camera_presets instead of patching the settings blob.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from grawji import camera_presets, fs_recipe
from grawji.backup_recipe import (
    BackupWriteError,
    apply_checksum,
    layout_for,
    read_names,
    unsupported_fields,
    write_name,
    write_recipes,
)
from grawji.camera_types import BackupTransferError, Camera, TransferResult
from grawji.recipe import Recipe

_log = logging.getLogger(__name__)

# Standard PTP / Fuji operation codes
_GET_DEVICE_INFO = 0x1001
_GET_DEVICE_PROP_VALUE = 0x1015
_GET_OBJECT_INFO = 0x1008
_GET_OBJECT = 0x1009
_SEND_OBJECT_INFO = 0x100C
_SEND_OBJECT = 0x100D

_USB_MODE_PROP = 0xD16E
_BACKUP_HANDLE = 0
_OBJECT_FORMAT = 0x5000
_PTP_OK = 0x2001

# Exactly the ObjectInfo dataset length X Acquire sends
_OBJECTINFO_SIZE = 1076

# Blob header: model is a NUL-terminated ASCII string at 0x14.
_MODEL_OFFSET = 0x14
_SERIAL_OFFSET = 0x34
_MAGIC = b"FUJIFILM"


def model_from_blob(blob: bytes) -> str | None:
    """Return the camera model from a backup blob header, or None.

    None means the blob is not a recognizable settings backup.
    """
    if not blob.startswith(_MAGIC):
        return None
    raw = blob[_MODEL_OFFSET:_SERIAL_OFFSET]
    model = raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
    return model or None


def _object_info(size: int) -> bytes:
    """A minimal PTP ObjectInfo dataset for the settings blob."""
    info = bytearray(_OBJECTINFO_SIZE)
    struct.pack_into("<I", info, 0, 0)  # StorageID
    struct.pack_into("<H", info, 4, _OBJECT_FORMAT)
    struct.pack_into("<H", info, 6, 0)  # ProtectionStatus
    struct.pack_into("<I", info, 8, size)  # ObjectCompressedSize
    return bytes(info)


def _check(code: int, what: str) -> None:
    """Raise if a PTP response code is not OK."""
    if code != _PTP_OK:
        raise BackupTransferError(f"{what} failed: 0x{code:04x}")


@dataclass(frozen=True)
class DeviceInfo:
    """What the camera's DeviceInfo advertises, as far as we parse it."""

    model: str | None = None
    props: frozenset[int] = frozenset()


def _read_ptp_string(data: bytes, off: int) -> tuple[str, int]:
    """Decode a PTP string at off."""
    count = data[off]
    off += 1
    raw = data[off : off + count * 2]
    return raw.decode("utf-16-le", "replace").rstrip("\x00"), off + count * 2


def _read_u16_array(data: bytes, off: int) -> tuple[list[int], int]:
    """Decode a PTP u16 array at off."""
    (count,) = struct.unpack_from("<I", data, off)
    off += 4
    values = list(struct.unpack_from(f"<{count}H", data, off))
    return values, off + count * 2


def parse_device_info(data: bytes) -> DeviceInfo:
    """Parse the model and advertised properties from a DeviceInfo."""
    try:
        # StandardVersion, VendorExtensionID, VendorExtensionVersion.
        off = 2 + 4 + 2
        _desc, off = _read_ptp_string(data, off)
        off += 2  # FunctionalMode
        _ops, off = _read_u16_array(data, off)
        _events, off = _read_u16_array(data, off)
        props, off = _read_u16_array(data, off)
        for _ in range(2):  # CaptureFormats, ImageFormats
            _fmt, off = _read_u16_array(data, off)
        _manufacturer, off = _read_ptp_string(data, off)
        model, off = _read_ptp_string(data, off)
        return DeviceInfo(model=model or None, props=frozenset(props))
    except (IndexError, struct.error):
        return DeviceInfo()


def setup(cam: Camera) -> DeviceInfo:
    """Run the preamble the camera requires before object access."""
    code, _p, data = cam.send_command(_GET_DEVICE_INFO)
    _check(code, "GetDeviceInfo")
    cam.send_command(_GET_DEVICE_PROP_VALUE, [_USB_MODE_PROP])
    return parse_device_info(data)


def read_backup(cam: Camera) -> bytes:
    """Download the settings backup blob (read-only)."""
    code, _p, _info = cam.send_command(_GET_OBJECT_INFO, [_BACKUP_HANDLE])
    _check(code, "GetObjectInfo")
    code, _p, blob = cam.send_command(_GET_OBJECT, [_BACKUP_HANDLE])
    _check(code, "GetObject")
    return blob


def restore_backup(cam: Camera, blob: bytes) -> None:
    """Upload a settings blob to the camera. WRITES PERSISTENT SETTINGS."""
    code, _p = cam.send_data_command(
        _SEND_OBJECT_INFO, [0, 0], _object_info(len(blob))
    )
    _check(code, "SendObjectInfo")
    code, _p = cam.send_data_command(_SEND_OBJECT, [], blob)
    _check(code, "SendObject")


class _HasVolatile(Protocol):
    """Anything carrying the offsets the camera rewrites on a restore."""

    @property
    def volatile_offsets(self) -> frozenset[int]:
        """Blob offsets the camera maintains itself."""
        ...


def classify_readback(
    before: bytes, after: bytes, target: bytes, layout: _HasVolatile
) -> tuple[list[int], list[int], list[int]]:
    """Classify how a written blob came back from the camera."""
    limit = min(len(before), len(target), len(after))
    applied: list[int] = []
    ignored: list[int] = []
    maintained: list[int] = []
    for i in range(limit):
        if before[i] == target[i]:
            continue  # not an intended change
        if after[i] == target[i]:
            applied.append(i)
        elif i in layout.volatile_offsets:
            maintained.append(i)
        elif after[i] == before[i]:
            ignored.append(i)
        else:
            maintained.append(i)
    return applied, ignored, maintained


def _read_once(
    connect: Callable[[], Camera],
    disconnect: Callable[[Camera], None],
    run_setup: bool,
) -> bytes:
    """Open a fresh connection, download the blob, and disconnect."""
    cam = connect()
    try:
        if run_setup:
            setup(cam)
        return read_backup(cam)
    finally:
        disconnect(cam)


def read_bank_names(
    connect: Callable[[], Camera],
    disconnect: Callable[[Camera], None],
    *,
    run_setup: bool = True,
) -> list[str]:
    """Return the connected body's current bank names, or [] if none."""
    cam = connect()
    try:
        info = setup(cam) if run_setup else DeviceInfo()
        if camera_presets.supports_presets(info.props):
            return camera_presets.read_preset_names(cam)
        before = read_backup(cam)
    finally:
        disconnect(cam)
    layout = layout_for(model_from_blob(before))
    if layout is None:
        return []
    return read_names(before, layout)


def transfer_recipes(
    connect: Callable[[], Camera],
    disconnect: Callable[[Camera], None],
    assignments: dict[int, Recipe],
    *,
    names: dict[int, str] | None = None,
    run_setup: bool = True,
) -> TransferResult:
    """Write recipes (and optional names) into the camera's custom banks.

    A gen5 body (preset properties advertised) is written over its first
    connection and returns early. On the blob path each phase opens its
    own connection: the camera rejects a GetObject and a SendObject in
    one session with 0x200f, so download, restore and read-back must be
    separate connects.

    Args:
        connect: Returns a freshly connected camera.
        disconnect: Tears a camera down.
        assignments: Bank index (0-based) -> recipe to store there.
        names: Optional bank index -> new name.
        run_setup: Run the PTP preamble first.

    Returns:
        A TransferResult describing what was written and verified.

    Raises:
        BackupTransferError: On an unsupported body, a PTP error, or a
            silently ignored write.
        BackupWriteError: If a recipe holds a value with no verified code.
    """
    if not assignments and not names:
        raise BackupTransferError("no bank assignments to write")

    cam = connect()
    try:
        info = setup(cam) if run_setup else DeviceInfo()
        if camera_presets.supports_presets(info.props):
            return camera_presets.transfer_presets(
                cam, assignments, names=names, model=info.model
            )
        before = read_backup(cam)
    finally:
        disconnect(cam)
    model = model_from_blob(before)
    layout = layout_for(model)
    _log.debug("backup: model=%r blob=%d bytes", model, len(before))
    if layout is None:
        raise BackupTransferError(
            f"body {model!r} has no verified bank layout; refusing to write"
        )

    dropped = {
        slot: unsupported_fields(layout, recipe)
        for slot, recipe in assignments.items()
    }
    dropped = {slot: fields for slot, fields in dropped.items() if fields}
    if dropped:
        _log.info("backup: dropping unsupported features %s", dropped)

    # write_recipes re-checks the blob size, so a relative whose blob is
    # not the expected length raises here before any restore.
    target = write_recipes(before, layout, assignments)
    if names and layout.name_rel is None:
        # gen3 banks are not user-nameable: drop the names, keep the rest.
        _log.info("backup: body has no bank names; dropping %s", names)
    elif names:
        for slot, name in names.items():
            target = write_name(target, layout, slot, name)
    # Recompute the checksum the camera validates (else it rejects with
    # 0x200f) no-op on bodies without one.
    target = apply_checksum(target, layout.checksum)
    _log.debug(
        "backup: restoring %d bytes (%d slots)",
        len(target),
        len(assignments),
    )

    cam = connect()
    try:
        if run_setup:
            setup(cam)
        restore_backup(cam, target)
    except BackupTransferError as exc:
        raise BackupTransferError(
            f"{exc} [model {model}, {len(target)} bytes]"
        ) from exc
    finally:
        disconnect(cam)

    after = _read_once(connect, disconnect, run_setup)
    applied, ignored, maintained = classify_readback(
        before, after, target, layout
    )
    _log.debug(
        "backup: applied=%d ignored=%d maintained=%d",
        len(applied),
        len(ignored),
        len(maintained),
    )
    if ignored:
        preview = ", ".join(f"@{o}" for o in ignored[:8])
        raise BackupTransferError(
            f"camera ACKed but silently ignored {len(ignored)} byte(s) "
            f"({preview}); settings not fully written"
        )
    return TransferResult(
        model=model or "",
        slots=tuple(sorted(set(assignments) | set(names or {}))),
        applied=len(applied),
        maintained=tuple(maintained),
        dropped=dropped,
    )


def transfer_fs_recipes(
    connect: Callable[[], Camera],
    disconnect: Callable[[Camera], None],
    assignments: dict[int, Recipe],
    *,
    run_setup: bool = True,
) -> TransferResult:
    """Write recipes into the body's FS1-FSn film-sim dial positions.

    These positions are NOT reachable over the gen5 preset properties
    (the slot selector refuses anything past C7), so they go through the
    settings-blob restore path even on gen5 bodies, using fs_recipe's
    verified per-parameter-array offsets. Each phase opens its own
    connection, like the C1-C7 blob path.

    Args:
        connect: Returns a freshly connected camera.
        disconnect: Tears a camera down.
        assignments: FS index (0-based, FS1=0) -> recipe to store there.
        run_setup: Run the PTP preamble first.

    Raises:
        BackupTransferError: On an unmapped body, a PTP error, or a
            silently ignored write.
        BackupWriteError: On a slot or blob-size mismatch.
    """
    if not assignments:
        raise BackupTransferError("no FS assignments to write")

    before = _read_once(connect, disconnect, run_setup)
    model = model_from_blob(before)
    layout = fs_recipe.layout_for(model)
    if layout is None:
        raise BackupTransferError(
            f"body {model!r} has no mapped FS dial layout; refusing to write"
        )

    dropped = {
        slot: fs_recipe.unsupported_fields(layout, recipe)
        for slot, recipe in assignments.items()
    }
    dropped = {slot: fields for slot, fields in dropped.items() if fields}
    if dropped:
        _log.info("fs: dropping unsupported features %s", dropped)

    target = fs_recipe.write_fs_recipes(before, layout, assignments)

    cam = connect()
    try:
        if run_setup:
            setup(cam)
        restore_backup(cam, target)
    except BackupTransferError as exc:
        raise BackupTransferError(
            f"{exc} [model {model}, FS write, {len(target)} bytes]"
        ) from exc
    finally:
        disconnect(cam)

    after = _read_once(connect, disconnect, run_setup)
    applied, ignored, maintained = classify_readback(
        before, after, target, layout
    )
    if ignored:
        preview = ", ".join(f"@{o}" for o in ignored[:8])
        raise BackupTransferError(
            f"camera ACKed but silently ignored {len(ignored)} byte(s) "
            f"({preview}); FS settings not fully written"
        )
    return TransferResult(
        model=model or "",
        slots=tuple(sorted(assignments)),
        applied=len(applied),
        maintained=tuple(maintained),
        dropped=dropped,
    )


__all__ = [
    # BackupWriteError re-exported so callers catch both failure modes here.
    "BackupTransferError",
    "BackupWriteError",
    "Camera",
    "DeviceInfo",
    "TransferResult",
    "classify_readback",
    "model_from_blob",
    "parse_device_info",
    "read_backup",
    "read_bank_names",
    "restore_backup",
    "setup",
    "transfer_fs_recipes",
    "transfer_recipes",
]
