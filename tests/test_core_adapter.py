"""Tests for the rawji adapter (RMW helpers + CameraSession)."""

import struct

import pytest
from rawji.fuji_enums import ev_to_int
from rawji.fuji_profile import encode_tone_value

from grawji.core import (
    OFFSET_FILM_SIM,
    CameraError,
    CameraSession,
    ForeignRafError,
    SessionStateError,
    apply_recipe,
    film_simulation_byte,
    recipe_from_profile,
    rmw_patch,
)
from grawji.recipe import Recipe

VELVIA_BYTE = 2
ACROS_BYTE = 12

# Profile offsets (PROFILE_PARAMS_OFFSET 513 + index*4) per rawji's layout.
OFF_EXPOSURE = 529
OFF_DYNAMIC_RANGE = 533
OFF_GRAIN = 545
OFF_WB_SHOOTCOND = 553
OFF_WHITE_BALANCE = 557
OFF_WB_SHIFT_R = 561
OFF_WB_SHIFT_B = 565
OFF_WB_COLOR_TEMP = 569
OFF_HIGHLIGHTS = 573
OFF_COLOR = 581


def _u32(profile, offset):
    """Read the little-endian uint32 parameter at ``offset``."""
    return struct.unpack("<I", profile[offset : offset + 4])[0]


class FakeCamera:
    """A stand-in for rawji.FujiCamera that records the call sequence."""

    def __init__(self, *, connect_ok=True, fail_on=None, profile=None):
        """Configure connect success, a method to fail on, and a profile."""
        self.calls = []
        self._connect_ok = connect_ok
        self._fail_on = fail_on  # method name that should raise 0x2002
        self._profile = profile if profile is not None else bytes(600)
        self.set_profiles = []
        self.last_full_resolution = None

    def _maybe_fail(self, name):
        """Raise a 0x2002-style error if this method is the failure point."""
        if self._fail_on == name:
            raise RuntimeError("PTP GetDevicePropValue failed: 0x2002")

    def connect(self):
        """Record the call and report the configured connect result."""
        self.calls.append("connect")
        return self._connect_ok

    def send_raf(self, filepath):
        """Record the RAF upload and maybe fail."""
        self.calls.append(("send_raf", filepath))
        self._maybe_fail("send_raf")

    def get_profile(self):
        """Record the profile read, maybe fail, and return the profile."""
        self.calls.append("get_profile")
        self._maybe_fail("get_profile")
        return self._profile

    def set_profile(self, profile):
        """Record the profile that was written."""
        self.calls.append("set_profile")
        self.set_profiles.append(profile)

    def trigger_conversion(self, full_resolution=True):
        """Record the trigger and the requested resolution."""
        self.calls.append(("trigger_conversion", full_resolution))
        self.last_full_resolution = full_resolution

    def wait_for_result(self, timeout=30):
        """Record the wait and return placeholder JPEG bytes."""
        self.calls.append(("wait_for_result", timeout))
        return b"JPEG"

    def disconnect(self):
        """Record the disconnect."""
        self.calls.append("disconnect")


def session_for(camera):
    """Build a CameraSession whose factory always returns ``camera``."""
    return CameraSession(camera_factory=lambda: camera)


def test_rmw_patch_sets_film_sim_byte():
    """rmw_patch writes the film-sim byte and leaves the rest intact."""
    base = bytes(OFFSET_FILM_SIM + 10)
    patched = rmw_patch(base, film_sim_byte=VELVIA_BYTE)
    assert patched[OFFSET_FILM_SIM] == VELVIA_BYTE
    assert patched[:OFFSET_FILM_SIM] == base[:OFFSET_FILM_SIM]
    assert patched[OFFSET_FILM_SIM + 1 :] == base[OFFSET_FILM_SIM + 1 :]


def test_rmw_patch_does_not_mutate_input():
    """rmw_patch returns a new buffer and never mutates the input."""
    base = bytes(OFFSET_FILM_SIM + 10)
    rmw_patch(base, film_sim_byte=ACROS_BYTE)
    assert base[OFFSET_FILM_SIM] == 0x00


def test_rmw_patch_rejects_short_profile():
    """rmw_patch rejects a profile too short to hold the offset."""
    with pytest.raises(ValueError, match="too short"):
        rmw_patch(bytes(10), film_sim_byte=VELVIA_BYTE)


def test_film_simulation_byte_known():
    """Known film-simulation names map to their profile bytes."""
    assert film_simulation_byte("Velvia") == VELVIA_BYTE
    assert film_simulation_byte("Acros") == ACROS_BYTE


def test_film_simulation_byte_unknown():
    """An unknown film-simulation name raises ValueError."""
    with pytest.raises(ValueError, match=r"film simulation"):
        film_simulation_byte("Nope")


def test_apply_recipe_patches_film_sim():
    """apply_recipe patches the recipe's film simulation into the profile."""
    base = bytes(600)
    patched = apply_recipe(base, Recipe(film_simulation="Velvia"))
    assert patched[OFFSET_FILM_SIM] == VELVIA_BYTE


def test_apply_recipe_patches_enums():
    """White balance and dynamic range use rawji's enum profile values."""
    base = bytes(600)
    patched = apply_recipe(
        base, Recipe(white_balance="Daylight", dynamic_range="DR400")
    )
    assert _u32(patched, OFF_WHITE_BALANCE) == 4  # WhiteBalance.Daylight
    assert _u32(patched, OFF_WB_SHOOTCOND) == 2  # gating: use manual WB
    assert _u32(patched, OFF_DYNAMIC_RANGE) == 3  # DynamicRange.DR400


def test_apply_recipe_asshot_leaves_white_balance_untouched():
    """AsShot does not write the WB fields (keeps the RAF's own WB)."""
    base = bytearray(600)
    struct.pack_into("<I", base, OFF_WB_SHOOTCOND, 1)  # native as-shot
    struct.pack_into("<I", base, OFF_WHITE_BALANCE, 7)  # arbitrary marker
    patched = apply_recipe(bytes(base), Recipe(white_balance="AsShot"))
    assert _u32(patched, OFF_WB_SHOOTCOND) == 1
    assert _u32(patched, OFF_WHITE_BALANCE) == 7


def test_apply_recipe_encodes_tone_values():
    """Tone params are scaled by encode_tone_value; negatives wrap to u32."""
    base = bytes(600)
    patched = apply_recipe(base, Recipe(highlights=2, color=-4))
    assert _u32(patched, OFF_HIGHLIGHTS) == encode_tone_value(2)
    expected_color = (1 << 32) + encode_tone_value(-4)
    assert _u32(patched, OFF_COLOR) == expected_color


def test_apply_recipe_validates_range():
    """An out-of-range tone value is rejected by rawji.validate_params."""
    with pytest.raises(ValueError, match="Highlights out of range"):
        apply_recipe(bytes(600), Recipe(highlights=10))


def test_apply_recipe_rejects_unknown_names():
    """Unknown white-balance / dynamic-range names raise ValueError."""
    with pytest.raises(ValueError, match=r"white balance"):
        apply_recipe(bytes(600), Recipe(white_balance="Nope"))
    with pytest.raises(ValueError, match=r"dynamic range"):
        apply_recipe(bytes(600), Recipe(dynamic_range="DR999"))


def test_apply_recipe_patches_exposure_and_grain():
    """Exposure uses EV encoding; grain uses its enum value."""
    base = bytes(600)
    patched = apply_recipe(base, Recipe(exposure=1.0, grain="Strong"))
    assert _u32(patched, OFF_EXPOSURE) == ev_to_int(1.0)  # +1 EV -> 1000
    assert _u32(patched, OFF_GRAIN) == 3  # GrainEffect.Strong


def test_apply_recipe_negative_exposure_wraps():
    """Negative exposure wraps to unsigned like the tone params."""
    base = bytes(600)
    patched = apply_recipe(base, Recipe(exposure=-1.0))
    assert _u32(patched, OFF_EXPOSURE) == (1 << 32) + ev_to_int(-1.0)


def test_apply_recipe_patches_wb_shift():
    """WB shift R/B are written raw, with negatives wrapped to u32."""
    base = bytes(600)
    patched = apply_recipe(base, Recipe(wb_shift_r=9, wb_shift_b=-9))
    assert _u32(patched, OFF_WB_SHIFT_R) == 9
    assert _u32(patched, OFF_WB_SHIFT_B) == (1 << 32) - 9


def test_color_temp_only_written_in_temperature_mode():
    """Colour temp is written only when white balance is Temperature."""
    base = bytes(600)
    in_temp = apply_recipe(
        base, Recipe(white_balance="Temperature", color_temp=8000)
    )
    assert _u32(in_temp, OFF_WB_COLOR_TEMP) == 8000

    other = apply_recipe(
        base, Recipe(white_balance="Daylight", color_temp=8000)
    )
    assert _u32(other, OFF_WB_COLOR_TEMP) == 0  # untouched base byte


def test_apply_recipe_rejects_short_profile():
    """A profile too short for a parameter offset is rejected."""
    with pytest.raises(ValueError, match="too short"):
        apply_recipe(bytes(100), Recipe(film_simulation="Velvia"))


def test_recipe_round_trips_through_profile():
    """recipe_from_profile is the exact inverse of apply_recipe."""
    recipe = Recipe(
        film_simulation="Velvia",
        white_balance="Temperature",
        dynamic_range="DR400",
        grain="Strong",
        exposure=1.0,
        highlights=2,
        shadows=-1,
        color=3,
        sharpness=-2,
        wb_shift_r=4,
        wb_shift_b=-5,
        color_temp=7000,
    )
    profile = apply_recipe(bytes(600), recipe)
    assert recipe_from_profile(profile) == recipe


def test_recipe_from_profile_falls_back_on_unknown_enum():
    """An enum value not in rawji's enum falls back to the default."""
    base = bytearray(apply_recipe(bytes(600), Recipe()))
    struct.pack_into("<I", base, OFF_WHITE_BALANCE, 0xABCD)  # not a WB value
    assert recipe_from_profile(bytes(base)).white_balance == "AsShot"


def test_open_call_order():
    """open() connects, sends the RAF, then reads the profile, in order."""
    cam = FakeCamera()
    session = session_for(cam)
    session.open("/tmp/shot.RAF")
    assert cam.calls == [
        "connect",
        ("send_raf", "/tmp/shot.RAF"),
        "get_profile",
    ]
    assert session.is_open
    assert session.raf_path.name == "shot.RAF"


def test_render_does_not_resend_raf():
    """render() applies the recipe without re-uploading the RAF."""
    cam = FakeCamera()
    session = session_for(cam)
    session.open("/tmp/shot.RAF")
    jpeg = session.render(
        Recipe(film_simulation="Velvia"), full_resolution=False
    )

    assert jpeg == b"JPEG"
    # send_raf happened exactly once - during open, not during render.
    assert sum(c == ("send_raf", "/tmp/shot.RAF") for c in cam.calls) == 1
    # The render patched the film-sim byte into the profile it set.
    assert cam.set_profiles[-1][OFFSET_FILM_SIM] == VELVIA_BYTE
    assert cam.last_full_resolution is False


def test_render_passes_full_resolution_flag():
    """render() forwards the full_resolution flag to the camera."""
    cam = FakeCamera()
    session = session_for(cam)
    session.open("/tmp/shot.RAF")
    session.render(Recipe(), full_resolution=True)
    assert cam.last_full_resolution is True


def test_render_before_open_raises():
    """Rendering before opening a RAF raises SessionStateError."""
    session = session_for(FakeCamera())
    with pytest.raises(SessionStateError):
        session.render(Recipe(), full_resolution=False)


def test_connect_failure_raises_camera_error():
    """A failed connect raises CameraError and leaves the session closed."""
    session = session_for(FakeCamera(connect_ok=False))
    with pytest.raises(CameraError):
        session.open("/tmp/shot.RAF")
    assert not session.is_open


def test_foreign_raf_raises_and_cleans_up():
    """A 0x2002 error becomes ForeignRafError and the camera disconnects."""
    cam = FakeCamera(fail_on="get_profile")
    session = session_for(cam)
    with pytest.raises(ForeignRafError):
        session.open("/tmp/foreign.RAF")
    assert not session.is_open
    assert "disconnect" in cam.calls  # half-open session cleaned up


def test_close_is_idempotent_and_disconnects():
    """close() disconnects once and is safe to call again."""
    cam = FakeCamera()
    session = session_for(cam)
    session.open("/tmp/shot.RAF")
    session.close()
    session.close()  # second call must not raise
    assert not session.is_open
    assert cam.calls.count("disconnect") == 1


def test_context_manager_closes():
    """Leaving the context manager closes the session."""
    cam = FakeCamera()
    with session_for(cam) as session:
        session.open("/tmp/shot.RAF")
    assert "disconnect" in cam.calls
