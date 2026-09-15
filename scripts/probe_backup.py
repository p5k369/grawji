"""Probe the camera settings backup/restore over USB.

Usage:
    python scripts/probe_backup.py download backup.bin
    python scripts/probe_backup.py diff before.bin after.bin
    python scripts/probe_backup.py restore backup.bin --write-settings
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import rawji

# Standard PTP operation codes.
GET_DEVICE_INFO = 0x1001
GET_DEVICE_PROP_VALUE = 0x1015
GET_OBJECT_INFO = 0x1008
GET_OBJECT = 0x1009
SEND_OBJECT_INFO = 0x100C
SEND_OBJECT = 0x100D

# Fuji USBMode property. 6 = raw conversion / backup restore.
USB_MODE = 0xD16E

# Fuji exposes the settings backup as this fixed object handle.
BACKUP_HANDLE = 0

# PTP response code for a successful operation.
PTP_OK = 0x2001

# ObjectCompressedSize sits after StorageID u32, ObjectFormat u16 and
# ProtectionStatus u16 in a PTP ObjectInfo dataset.
SIZE_OFFSET = 8

# DeviceInfo prefix: StandardVersion u16, VendorExtensionID u32,
# VendorExtensionVersion u16, then the VendorExtensionDesc string.
_DEVINFO_STRING_OFFSET = 8


def _connect() -> rawji.FujiCamera:
    """Connect to the camera."""
    cam = rawji.FujiCamera()
    if not cam.connect():
        sys.exit("could not connect (camera in RAW CONV / BACKUP mode?)")
    return cam


def _supported_ops(di: bytes) -> list[int]:
    """Parse OperationsSupported from a DeviceInfo dataset."""
    off = _DEVINFO_STRING_OFFSET
    chars = di[off]
    off += 1 + chars * 2
    off += 2
    (count,) = struct.unpack_from("<I", di, off)
    off += struct.calcsize("<I")
    return list(struct.unpack_from("<" + "H" * count, di, off))


def _setup(cam: rawji.FujiCamera) -> None:
    """Mirror libfuji: read DeviceInfo and USBMode before object access."""
    code, _params, di = cam.send_command(GET_DEVICE_INFO)
    if code != PTP_OK:
        sys.exit(f"GetDeviceInfo failed: 0x{code:04x}")
    try:
        ops = _supported_ops(di)
        print("supported ops: " + " ".join(f"0x{op:04x}" for op in ops))
        for op in (GET_OBJECT_INFO, GET_OBJECT, SEND_OBJECT_INFO):
            mark = "yes" if op in ops else "NO"
            print(f"  0x{op:04x} advertised: {mark}")
    except (IndexError, struct.error) as exc:
        print(f"could not parse DeviceInfo ops: {exc}")
        print(f"DeviceInfo first 64 bytes:\n{di[:64].hex(' ', 4)}")
    code, _params, mode = cam.send_command(GET_DEVICE_PROP_VALUE, [USB_MODE])
    if code == PTP_OK and mode:
        print(f"USBMode raw: {mode.hex()} (expect 06 = raw conv/backup)")


def _object_size(info: bytes) -> int:
    """ObjectCompressedSize from a PTP ObjectInfo dataset."""
    if len(info) < SIZE_OFFSET + struct.calcsize("<I"):
        return 0
    (size,) = struct.unpack_from("<I", info, SIZE_OFFSET)
    return int(size)


def caps() -> None:
    """Report DeviceInfo supported operations and USBMode."""
    cam = _connect()
    try:
        _setup(cam)
    finally:
        cam.disconnect()


def _read_blob(cam: rawji.FujiCamera) -> tuple[bytes, int]:
    """Read the settings backup blob and its declared size."""
    code, _params, info = cam.send_command(GET_OBJECT_INFO, [BACKUP_HANDLE])
    if code != PTP_OK:
        sys.exit(f"GetObjectInfo failed: 0x{code:04x}")
    declared = _object_size(info)
    code, _params, blob = cam.send_command(GET_OBJECT, [BACKUP_HANDLE])
    if code != PTP_OK:
        sys.exit(f"GetObject failed: 0x{code:04x}")
    return blob, declared


def _download() -> bytes:
    """Connect, run setup and return the current backup blob."""
    cam = _connect()
    try:
        _setup(cam)
        blob, _declared = _read_blob(cam)
    finally:
        cam.disconnect()
    return blob


def download(path: str) -> None:
    """Download the settings backup blob and archive it."""
    cam = _connect()
    try:
        _setup(cam)
        blob, declared = _read_blob(cam)
    finally:
        cam.disconnect()

    Path(path).write_bytes(blob)
    print(f"wrote {len(blob)} bytes to {path} (ObjectInfo size {declared})")
    print(f"first 64 bytes:\n{blob[:64].hex(' ', 4)}")


def _emit_run(a: bytes, b: bytes, start: int, end: int) -> None:
    """Print one differing byte range."""
    print(
        f"  @{start}-{end}: {a[start : end + 1].hex()} -> "
        f"{b[start : end + 1].hex()}"
    )


def diff(path_a: str, path_b: str) -> None:
    """Show byte ranges that differ between two backups."""
    a = Path(path_a).read_bytes()
    b = Path(path_b).read_bytes()
    if len(a) != len(b):
        print(f"lengths differ: {len(a)} vs {len(b)}")
    limit = min(len(a), len(b))
    run_start = -1
    changes = 0
    for i in range(limit):
        if a[i] != b[i]:
            if run_start < 0:
                run_start = i
        elif run_start >= 0:
            _emit_run(a, b, run_start, i - 1)
            changes += 1
            run_start = -1
    if run_start >= 0:
        _emit_run(a, b, run_start, limit - 1)
        changes += 1
    print(f"{changes} differing run(s)")


def _object_info(size: int) -> bytes:
    """A minimal PTP ObjectInfo dataset for the settings blob."""
    info = bytearray(1076)
    struct.pack_into("<I", info, 0, 0)
    struct.pack_into("<H", info, 4, 0x5000)
    struct.pack_into("<H", info, 6, 0)
    struct.pack_into("<I", info, 8, size)
    return bytes(info)


def _intended_changes(before: bytes, target: bytes) -> list[int]:
    """Offsets where the blob we want to write differs from the camera."""
    limit = min(len(before), len(target))
    return [i for i in range(limit) if before[i] != target[i]]


def _verify_write(before: bytes, target: bytes) -> None:
    """Read the blob back and confirm the intended bytes actually took."""
    after = _download()
    intended = _intended_changes(before, target)
    if not intended:
        run = "byte-identical" if after == before else "differs (see diff)"
        print(f"identity restore; read-back {run}")
        return

    applied = [i for i in intended if i < len(after) and after[i] == target[i]]
    missed = [i for i in intended if i not in applied]
    # A silently ignored byte stays at its BEFORE value (observed on the X-E5).
    ignored = [i for i in missed if after[i] == before[i]]
    maintained = [i for i in missed if after[i] != before[i]]
    for i in maintained:
        print(
            f"  camera-maintained @{i}: wanted 0x{target[i]:02x}, "
            f"camera wrote 0x{after[i]:02x} (was 0x{before[i]:02x})"
        )
    for i in ignored:
        print(
            f"  NOT applied @{i}: wanted 0x{target[i]:02x}, "
            f"camera kept 0x{before[i]:02x}"
        )
    if ignored:
        sys.exit(
            f"WRITE FAILED: {len(applied)}/{len(intended)} intended byte(s) "
            "applied; the camera ACKed but silently ignored the rest."
        )
    print(
        f"write verified: {len(applied)} intended byte(s) applied"
        + (f", {len(maintained)} camera-maintained" if maintained else "")
    )


def restore(path: str, *, confirmed: bool) -> None:
    """Upload a backup blob to the camera."""
    if not confirmed:
        sys.exit(
            "restore writes the camera's settings and is disabled.\n"
            "Re-run with --write-settings once you have archived the "
            "current backup and an SD-card 'Save settings' fallback."
        )
    target = Path(path).read_bytes()
    before = _download()
    cam = _connect()
    try:
        _setup(cam)
        code, _params = cam.send_data_command(
            SEND_OBJECT_INFO, [0, 0], _object_info(len(target))
        )
        if code != PTP_OK:
            sys.exit(f"SendObjectInfo failed: 0x{code:04x}")
        code, _params = cam.send_data_command(SEND_OBJECT, [], target)
        if code != PTP_OK:
            sys.exit(f"SendObject failed: 0x{code:04x}")
    finally:
        cam.disconnect()
    print(f"restored {len(target)} bytes; verifying...")
    _verify_write(before, target)


def _parser() -> argparse.ArgumentParser:
    """Build the download / diff / restore command-line parser."""
    parser = argparse.ArgumentParser(description="Settings backup probe")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("caps", help="report supported ops and USBMode (read-only)")

    dl = sub.add_parser("download", help="download the backup (read-only)")
    dl.add_argument("path")

    df = sub.add_parser("diff", help="show byte ranges that differ")
    df.add_argument("before")
    df.add_argument("after")

    rs = sub.add_parser("restore", help="upload a backup (writes settings)")
    rs.add_argument("path")
    rs.add_argument("--write-settings", action="store_true")
    return parser


def main(argv: list[str]) -> None:
    """Dispatch download / diff / restore."""
    args = _parser().parse_args(argv[1:])
    if args.command == "caps":
        caps()
    elif args.command == "download":
        download(args.path)
    elif args.command == "diff":
        diff(args.before, args.after)
    elif args.command == "restore":
        restore(args.path, confirmed=args.write_settings)


if __name__ == "__main__":
    main(sys.argv)
