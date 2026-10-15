"""Collect a RAW conversion's result from the camera.

After StartRawConversion the camera lists the developed image as a PTP
object; it is downloaded, then deleted so the next conversion can run.
The object list can hold other things that must never be taken for the
result:

- the uploaded RAF (format 0xF802, "FUP_FILE.dat"), which the camera
  lists after an upload whose reply was lost and refuses to hand back
  (0x200F) - taking the first listed object, as rawji does, then fails
  every render until the camera is power-cycled;
- the result of an earlier conversion whose reply was lost.

So the result is picked by its ObjectInfo format among objects that are
new since the conversion started, leftovers are deleted first, and a
result is deleted even when its download fails. X RAW Studio likewise
reads each object's info before fetching it and retries refusals.
"""

from __future__ import annotations

import logging
import struct
import time
from collections.abc import Callable, Collection

from grawji.camera.camera_types import Camera

_log = logging.getLogger(__name__)

_GET_OBJECT_HANDLES = 0x1007
_GET_OBJECT_INFO = 0x1008
_GET_OBJECT = 0x1009
_GET_THUMB = 0x100A
_DELETE_OBJECT = 0x100B
_PTP_OK = 0x2001
_ACCESS_DENIED = 0x200F
_ALL_STORAGES = 0xFFFFFFFF

# ObjectFormat of the RAF uploaded for conversion (SendObjectInfo 0x900C).
UPLOAD_FORMAT = 0xF802

# GetObjectHandles data: a u32 count, then the handles.
_COUNT_SIZE = 4
# ObjectInfo dataset: u32 StorageID, then the u16 ObjectFormat.
_FORMAT_OFFSET = 4
_FORMAT_END = 6

# How often to look for the result while the camera develops it.
POLL_INTERVAL_S = 0.05


class ConversionError(RuntimeError):
    """The camera refused a step of collecting a conversion result."""


def object_handles(camera: Camera) -> list[int]:
    """Handles of every object the camera lists.

    Raises:
        ConversionError: If the camera refuses the listing.
    """
    code, _p, data = camera.send_command(
        _GET_OBJECT_HANDLES, [_ALL_STORAGES, 0, 0]
    )
    if code != _PTP_OK:
        raise ConversionError(f"GetObjectHandles failed: 0x{code:04X}")
    if len(data) < _COUNT_SIZE:
        return []
    (count,) = struct.unpack_from("<I", data)
    count = min(count, (len(data) - _COUNT_SIZE) // 4)
    return list(struct.unpack_from(f"<{count}I", data, _COUNT_SIZE))


def object_format(camera: Camera, handle: int) -> int | None:
    """An object's ObjectFormat, or None while the camera refuses it."""
    code, _p, data = camera.send_command(_GET_OBJECT_INFO, [handle])
    if code != _PTP_OK or len(data) < _FORMAT_END:
        return None
    return int(struct.unpack_from("<H", data, _FORMAT_OFFSET)[0])


def delete_object(camera: Camera, handle: int) -> bool:
    """Delete an object; a refusal is logged and reported, not raised."""
    code, _p, _d = camera.send_command(_DELETE_OBJECT, [handle])
    if code != _PTP_OK:
        _log.warning("DeleteObject(0x%X) failed: 0x%04X", handle, code)
        return False
    return True


def clear_leftovers(camera: Camera, *, keep_upload: bool) -> set[int]:
    """Delete objects left by earlier conversions or sessions.

    Best effort: refusals are logged, transport errors propagate.

    Args:
        camera: A connected camera.
        keep_upload: Spare the uploaded RAF and any object whose info the
            camera refuses - inside an open session, where the RAF is
            still needed. Before an upload every object is a leftover.

    Returns:
        The handles still listed, which a following wait must ignore.
    """
    try:
        handles = object_handles(camera)
    except ConversionError as exc:
        _log.warning("could not list leftover camera objects: %s", exc)
        return set()
    kept: set[int] = set()
    for handle in handles:
        if keep_upload and object_format(camera, handle) in (
            UPLOAD_FORMAT,
            None,
        ):
            kept.add(handle)
            continue
        _log.warning("deleting leftover camera object 0x%X", handle)
        if not delete_object(camera, handle):
            kept.add(handle)
    return kept


def wait_for_result(
    camera: Camera,
    timeout: float,
    *,
    thumbnail: bool = False,
    ignore: Collection[int] = (),
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """Wait for a started conversion's result, download it, delete it.

    Args:
        camera: The camera converting.
        timeout: Seconds to wait for the result and for its download.
        thumbnail: Download only the result's thumbnail, falling back to
            the full image where the body refuses GetThumb.
        ignore: Handles listed before the conversion started.
        clock: Monotonic clock, injectable for tests.
        sleep: Sleep function, injectable for tests.

    Returns:
        The result's bytes.

    Raises:
        TimeoutError: If no result appears within timeout.
        ConversionError: If the camera refuses the listing or download.
    """
    deadline = clock() + timeout

    def keep_trying() -> bool:
        """Sleep one poll interval, unless time is up."""
        if clock() >= deadline:
            return False
        sleep(POLL_INTERVAL_S)
        return True

    skip = set(ignore)
    while True:
        for handle in object_handles(camera):
            if handle in skip:
                continue
            fmt = object_format(camera, handle)
            if fmt == UPLOAD_FORMAT:
                skip.add(handle)
            elif fmt is not None:
                return _collect(
                    camera, handle, thumbnail=thumbnail, retry=keep_trying
                )
        if not keep_trying():
            raise TimeoutError(f"Conversion timeout after {timeout} seconds")


def _collect(
    camera: Camera,
    handle: int,
    *,
    thumbnail: bool,
    retry: Callable[[], bool],
) -> bytes:
    """Download a result, then delete it (also when the download fails).

    A transport error propagates without the delete: the session cannot
    be trusted with another command after one.
    """
    try:
        data = _download(camera, handle, thumbnail=thumbnail, retry=retry)
    except ConversionError:
        delete_object(camera, handle)
        raise
    delete_object(camera, handle)
    return data


def _download(
    camera: Camera,
    handle: int,
    *,
    thumbnail: bool,
    retry: Callable[[], bool],
) -> bytes:
    """GetThumb or GetObject, retrying while the camera refuses access."""
    if thumbnail:
        code, _p, data = camera.send_command(_GET_THUMB, [handle])
        if code == _PTP_OK and data:
            return data
        _log.info("GetThumb refused (0x%04X), downloading the image", code)
    while True:
        code, _p, data = camera.send_command(_GET_OBJECT, [handle])
        if code == _PTP_OK:
            return data
        if code != _ACCESS_DENIED or not retry():
            raise ConversionError(f"GetObject failed: 0x{code:04X}")
