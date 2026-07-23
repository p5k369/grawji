"""Tests for the USB backup transfer layer (fake camera, no hardware)."""

import struct

import pytest

from grawji.backup_recipe import LAYOUTS
from grawji.camera_backup import (
    BackupTransferError,
    classify_readback,
    model_from_blob,
    transfer_recipes,
)
from grawji.recipe import Recipe

_GET_OBJECT_INFO = 0x1008
_GET_OBJECT = 0x1009
_SEND_OBJECT_INFO = 0x100C
_SEND_OBJECT = 0x100D
_PTP_OK = 0x2001


def _make_blob(model: str, size: int) -> bytes:
    """A minimal settings blob with a valid header for the given model."""
    blob = bytearray(size)
    blob[:8] = b"FUJIFILM"
    blob[8:16] = b"X-BACKUP"
    blob[16:20] = b"0100"
    blob[0x14 : 0x14 + len(model)] = model.encode("ascii")
    return bytes(blob)


class FakeCamera:
    """In-memory PTP camera: serves a blob and accepts restores."""

    def __init__(self, blob, *, volatile=None, ignore=()):
        """Hold the blob plus volatile/ignore behaviour flags."""
        self.blob = bytearray(blob)
        self.volatile = volatile or {}
        self.ignore = set(ignore)
        self.restores = 0

    def send_command(self, code, params=None):
        """Serve the read ops and accept the preamble."""
        if code == _GET_OBJECT_INFO:
            info = bytearray(64)
            struct.pack_into("<I", info, 8, len(self.blob))
            return _PTP_OK, [], bytes(info)
        if code == _GET_OBJECT:
            return _PTP_OK, [], bytes(self.blob)
        return _PTP_OK, [], b""  # GetDeviceInfo / USBMode preamble

    def send_data_command(self, code, params, data):
        """Accept a restore, honouring ignore/volatile offsets."""
        if code == _SEND_OBJECT_INFO:
            return _PTP_OK, []
        if code == _SEND_OBJECT:
            incoming = bytearray(data)
            for off in self.ignore:
                incoming[off] = self.blob[off]  # refuse this byte
            for off, val in self.volatile.items():
                incoming[off] = val  # camera stamps its own value
            self.blob = incoming
            self.restores += 1
            return _PTP_OK, []
        return _PTP_OK, []


def _run(cam, assignments, **kwargs):
    """Drive transfer_recipes with a fake camera reused for every phase."""
    kwargs.setdefault("run_setup", False)
    return transfer_recipes(
        lambda: cam, lambda _c: None, assignments, **kwargs
    )


def test_model_from_blob():
    """The model parses from the blob header; non-blobs yield None."""
    assert model_from_blob(_make_blob("X-T3", 33404)) == "X-T3"
    assert model_from_blob(_make_blob("X100F", 5660)) == "X100F"
    assert model_from_blob(b"not a backup") is None


def test_names_dropped_on_body_without_bank_names():
    """On a nameless-bank body a name is dropped, not an error."""
    # gen3 (X100F) banks are not nameable: a name is dropped, not an error.
    cam = FakeCamera(_make_blob("X100F", 5660))
    result = _run(
        cam, {0: Recipe(film_simulation="Velvia")}, names={0: "KODAK"}
    )
    layout = LAYOUTS["X100F"]
    assert cam.blob[layout.sim0] == 3  # recipe still written
    assert result.model == "X100F"


def test_transfer_writes_and_verifies_x100f():
    """A transfer patches the slots and verifies them on the camera."""
    cam = FakeCamera(_make_blob("X100F", 5660))
    result = _run(
        cam,
        {
            0: Recipe(film_simulation="Velvia"),
            4: Recipe(film_simulation="Acros"),
        },
    )
    assert result.model == "X100F"
    assert result.slots == (0, 4)
    assert result.applied > 0
    # The camera now holds the written recipe.
    layout = LAYOUTS["X100F"]
    assert cam.blob[layout.sim0] == 3  # Velvia
    assert cam.blob[layout.sim0 + 4 * layout.stride] == 14  # Acros


def test_transfer_tolerates_camera_maintained_fields():
    """Camera-stamped checksum/counter fields do not fail a transfer."""
    blob = _make_blob("X-T3", 33404)
    cam = FakeCamera(blob, volatile={176: 0x99, 248: 0x01, 3276: 0x1E})
    result = _run(cam, {0: Recipe(film_simulation="Velvia")})
    layout = LAYOUTS["XT3"]
    assert cam.blob[layout.sim0] == 3  # Velvia written
    assert result.applied > 0


def test_transfer_raises_on_silent_no_op():
    """A byte the camera silently keeps unchanged fails the transfer."""
    # The camera refuses the film-sim byte (offset sim0 of slot 0).
    layout = LAYOUTS["X100F"]
    cam = FakeCamera(_make_blob("X100F", 5660), ignore={layout.sim0})
    with pytest.raises(BackupTransferError, match="silently ignored"):
        _run(cam, {0: Recipe(film_simulation="Velvia")})


def test_transfer_rejects_unsupported_body():
    """A body without a verified layout is refused before writing."""
    cam = FakeCamera(_make_blob("X-E5", 70524))
    with pytest.raises(BackupTransferError, match="no verified bank layout"):
        _run(cam, {0: Recipe()})


def test_transfer_rejects_relative_with_wrong_blob_size():
    """A relative with an unexpected blob size is refused unwritten."""
    # A "close relative" mapped to gen3 but whose blob is the wrong size
    # is refused by the size guard before any write.
    from grawji.backup_recipe import BackupWriteError

    cam = FakeCamera(_make_blob("X-T2", 9999))
    with pytest.raises(BackupWriteError, match="expected"):
        _run(cam, {0: Recipe()})
    assert cam.restores == 0  # nothing was written


def test_empty_assignments_raise():
    """A transfer with nothing to write is refused."""
    cam = FakeCamera(_make_blob("X100F", 5660))
    with pytest.raises(BackupTransferError, match="no bank"):
        _run(cam, {})


def test_classify_readback_separates_ignored_from_maintained():
    """Read-back triage: applied vs ignored vs camera-maintained."""
    layout = LAYOUTS["XT3"]
    size = layout.blob_size
    before = bytearray(size)
    target = bytearray(size)
    after = bytearray(size)
    # Intended change at a normal bank byte that the camera applied.
    target[layout.sim0] = 3
    after[layout.sim0] = 3
    # Intended change at a volatile offset (@176) the camera overrode with
    # its own value -> maintained, not a failure.
    target[176] = 0x10
    after[176] = 0x42
    # Intended change the camera silently refused -> ignored (real no-op).
    target[layout.sim0 + 20] = 7
    after[layout.sim0 + 20] = before[layout.sim0 + 20]
    applied, ignored, maintained = classify_readback(
        bytes(before), bytes(after), bytes(target), layout
    )
    assert layout.sim0 in applied
    assert 176 in maintained
    assert layout.sim0 + 20 in ignored
