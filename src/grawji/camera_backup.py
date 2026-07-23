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
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from grawji.backup_recipe import (
    BackupWriteError,
    BankLayout,
    apply_checksum,
    layout_for,
    read_names,
    unsupported_fields,
    write_name,
    write_recipes,
)
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


class Camera(Protocol):
    """The subset of the rawji camera object this module uses."""

    def send_command(
        self, code: int, params: list[int] | None = ...
    ) -> tuple[int, list[int], bytes]:
        """Issue a PTP command; return (response code, params, data)."""
        ...

    def send_data_command(
        self, code: int, params: list[int], data: bytes
    ) -> tuple[int, list[int]]:
        """Issue a PTP command with a data phase; return (code, params)."""
        ...


class BackupTransferError(RuntimeError):
    """A backup transfer failed (unsupported body, PTP error, or no-op)."""


@dataclass(frozen=True)
class TransferResult:
    """Outcome of a recipe transfer.

    Attributes:
        model: The body model parsed from the blob.
        slots: The bank indices written.
        applied: Count of intended recipe bytes confirmed on the camera.
        maintained: Offsets the camera rewrote itself (checksum, counters).
        dropped: Recipe features the body could not store, per slot.
    """

    model: str
    slots: tuple[int, ...]
    applied: int
    maintained: tuple[int, ...]
    dropped: dict[int, list[str]]


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


def setup(cam: Camera) -> None:
    """Run the preamble the camera requires before object access."""
    code, _p, _d = cam.send_command(_GET_DEVICE_INFO)
    _check(code, "GetDeviceInfo")
    cam.send_command(_GET_DEVICE_PROP_VALUE, [_USB_MODE_PROP])


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


def classify_readback(
    before: bytes, after: bytes, target: bytes, layout: BankLayout
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
    before = _read_once(connect, disconnect, run_setup)
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

    Each phase opens its OWN connection: the camera rejects a GetObject
    and a SendObject in one session with 0x200f, so download, restore and
    read-back must be separate connects.

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

    before = _read_once(connect, disconnect, run_setup)
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


__all__ = [
    # BackupWriteError re-exported so callers catch both failure modes here.
    "BackupTransferError",
    "BackupWriteError",
    "Camera",
    "TransferResult",
    "classify_readback",
    "model_from_blob",
    "read_backup",
    "read_bank_names",
    "restore_backup",
    "setup",
    "transfer_recipes",
]
