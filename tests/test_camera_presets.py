"""Tests for the gen5 preset transfer layer."""

import struct
from dataclasses import dataclass, field

import pytest

from grawji.camera_backup import (
    BackupTransferError,
    parse_device_info,
    transfer_recipes,
)
from grawji.camera_presets import (
    read_preset_names,
    supports_presets,
    transfer_presets,
)
from grawji.preset_recipe import (
    PROP_CLARITY,
    PROP_COLOR,
    PROP_FILM_SIMULATION,
    PROP_GRAIN,
    PROP_PRESET_NAME,
    PROP_PRESET_SLOT,
    PROP_WB_SHIFT_R,
    PROP_WHITE_BALANCE,
)
from grawji.recipe import Recipe

_GET_DEVICE_INFO = 0x1001
_GET_PROP = 0x1015
_SET_PROP = 0x1016
_PTP_OK = 0x2001
_PTP_INVALID_VALUE = 0x201C


@pytest.fixture(autouse=True)
def _no_slot_switch_delay(monkeypatch):
    """Skip the real camera's slot-switch settling wait in tests."""
    monkeypatch.setattr("grawji.camera_presets._SLOT_SWITCH_DELAY_S", 0)


def _ptp_string(text: str) -> bytes:
    if not text:
        return b"\x00"
    encoded = (text + "\x00").encode("utf-16-le")
    return bytes([len(encoded) // 2]) + encoded


def _device_info(model: str, props: list[int]) -> bytes:
    """A minimal PTP DeviceInfo dataset advertising the given props."""
    out = bytearray()
    out += struct.pack("<HIH", 100, 0, 0)  # version, ext id, ext version
    out += _ptp_string("")  # VendorExtensionDesc
    out += struct.pack("<H", 0)  # FunctionalMode
    for array in ([0x1001], [], props, [], []):
        out += struct.pack("<I", len(array))
        out += struct.pack(f"<{len(array)}H", *array)
    out += _ptp_string("FUJIFILM")
    out += _ptp_string(model)
    return bytes(out)


@dataclass
class _Slot:
    """One fake preset slot: a name plus stored property values."""

    name: str
    props: dict[int, int] = field(default_factory=dict)


class FakePresetCamera:
    """In-memory gen5 body: 7 preset slots of device properties."""

    def __init__(self, *, reject=(), ignore=(), grain_quirk=False):
        """Slots start empty-ish; reject/ignore steer write behaviour."""
        self.slots = [_Slot(name=f"BANK{i + 1}") for i in range(7)]
        self.active = 3  # 1-based, like the camera
        self.reject = set(reject)
        self.ignore = set(ignore)
        self.grain_quirk = grain_quirk

    def _slot(self):
        return self.slots[self.active - 1]

    def send_command(self, code, params=None):
        """Serve DeviceInfo and property reads."""
        if code == _GET_DEVICE_INFO:
            props = [PROP_PRESET_SLOT, PROP_PRESET_NAME]
            return _PTP_OK, [], _device_info("X-E5", props)
        if code == _GET_PROP:
            prop = params[0]
            if prop == PROP_PRESET_SLOT:
                return _PTP_OK, [], struct.pack("<I", self.active)
            if prop == PROP_PRESET_NAME:
                return _PTP_OK, [], _ptp_string(self._slot().name)
            value = self._slot().props.get(prop)
            if value is None:
                return _PTP_OK, [], b""
            return _PTP_OK, [], struct.pack("<I", value)
        return _PTP_OK, [], b""

    def send_data_command(self, code, params, data):
        """Accept property writes, honouring reject/ignore/quirk."""
        if code != _SET_PROP:
            return _PTP_OK, []
        prop = params[0]
        if prop == PROP_PRESET_SLOT:
            self.active = struct.unpack_from("<H", data)[0]
            return _PTP_OK, []
        if prop == PROP_PRESET_NAME:
            count = data[0]
            text = data[1 : 1 + count * 2].decode("utf-16-le")
            self._slot().name = text.rstrip("\x00")
            return _PTP_OK, []
        if prop in self.reject:
            return _PTP_INVALID_VALUE, []
        if prop in self.ignore:
            return _PTP_OK, []  # ACK without storing
        value = struct.unpack_from("<H", data)[0]
        if self.grain_quirk and prop == PROP_GRAIN and value == 1:
            value = 6  # the X-T5 family stores 6 for grain Off
        self._slot().props[prop] = value
        return _PTP_OK, []


def test_supports_presets_needs_the_slot_prop():
    """Only bodies advertising 0xD18C take the preset path."""
    assert supports_presets(frozenset({PROP_PRESET_SLOT, 0xD185}))
    assert not supports_presets(frozenset({0xD185}))


def test_parse_device_info_reads_model_and_props():
    """The DeviceInfo parser finds the model and the property list."""
    info = parse_device_info(_device_info("X-E5", [PROP_PRESET_SLOT]))
    assert info.model == "X-E5"
    assert PROP_PRESET_SLOT in info.props
    assert parse_device_info(b"junk").props == frozenset()


def test_transfer_writes_and_verifies_a_slot():
    """A recipe lands in the selected slot's properties, verified."""
    cam = FakePresetCamera()
    result = transfer_presets(
        cam,
        {0: Recipe(film_simulation="Velvia", white_balance="Daylight")},
        names={0: "KODAK"},
        model="X-E5",
    )
    assert result.model == "X-E5"
    assert result.slots == (0,)
    assert result.applied > 0
    assert not result.dropped
    slot = cam.slots[0]
    assert slot.name == "KODAK"
    assert slot.props[PROP_FILM_SIMULATION] == 2
    assert slot.props[PROP_WHITE_BALANCE] == 0x0004
    # The user's active slot selection was restored.
    assert cam.active == 3


def test_as_shot_leaves_the_slot_wb_untouched():
    """An AsShot WB round-trips whatever WB the slot already held."""
    cam = FakePresetCamera()
    cam.slots[1].props[PROP_WHITE_BALANCE] = 0x8006  # Shade
    cam.slots[1].props[PROP_WB_SHIFT_R] = 5
    transfer_presets(cam, {1: Recipe()})
    assert cam.slots[1].props[PROP_WHITE_BALANCE] == 0x8006
    assert cam.slots[1].props[PROP_WB_SHIFT_R] == 5


def test_mono_recipe_omits_color():
    """A B&W recipe never writes the dual-use colour property."""
    cam = FakePresetCamera()
    transfer_presets(cam, {2: Recipe(film_simulation="Acros")})
    assert PROP_COLOR not in cam.slots[2].props


def test_grain_off_storage_quirk_is_not_a_failure():
    """A body storing grain Off as 6 still verifies."""
    cam = FakePresetCamera(grain_quirk=True)
    result = transfer_presets(cam, {0: Recipe(grain="Off")})
    assert cam.slots[0].props[PROP_GRAIN] == 6
    assert result.applied > 0


def test_silently_ignored_property_raises():
    """An ACKed write that keeps the old value fails the transfer."""
    cam = FakePresetCamera(ignore={PROP_FILM_SIMULATION})
    cam.slots[0].props[PROP_FILM_SIMULATION] = 12  # stored Acros
    with pytest.raises(BackupTransferError, match="silently ignored"):
        transfer_presets(cam, {0: Recipe(film_simulation="Velvia")})


def test_rejected_clarity_is_dropped_not_fatal():
    """The known X-T5 clarity rejection becomes a dropped note."""
    cam = FakePresetCamera(reject={PROP_CLARITY})
    result = transfer_presets(cam, {0: Recipe(clarity=3)})
    assert "clarity" in " ".join(result.dropped[0])
    assert cam.slots[0].props[PROP_FILM_SIMULATION] == 1


def test_other_rejections_are_fatal():
    """An unexpected property rejection stops the transfer."""
    cam = FakePresetCamera(reject={PROP_FILM_SIMULATION})
    with pytest.raises(BackupTransferError, match="rejected"):
        transfer_presets(cam, {0: Recipe(film_simulation="Velvia")})


def test_slot_range_is_validated():
    """Out-of-range bank indices are refused before any write."""
    cam = FakePresetCamera()
    with pytest.raises(BackupTransferError, match="out of range"):
        transfer_presets(cam, {7: Recipe()})


def test_read_preset_names_restores_the_active_slot():
    """Names read across all slots, then the selection goes back."""
    cam = FakePresetCamera()
    names = read_preset_names(cam)
    assert names == [f"BANK{i + 1}" for i in range(7)]
    assert cam.active == 3


def test_transfer_recipes_dispatches_to_the_preset_path():
    """A body advertising 0xD18C routes through the property writer."""
    cam = FakePresetCamera()
    result = transfer_recipes(
        lambda: cam,
        lambda _c: None,
        {0: Recipe(film_simulation="ClassicNeg")},
        names={0: "PACIFIC"},
    )
    assert result.model == "X-E5"
    assert cam.slots[0].props[PROP_FILM_SIMULATION] == 17
    assert cam.slots[0].name == "PACIFIC"
