"""Tests for the rawji adapter (RMW helpers + CameraSession)."""

import pytest

from grawji.core import (
    OFFSET_FILM_SIM,
    CameraError,
    CameraSession,
    ForeignRafError,
    SessionStateError,
    apply_recipe,
    film_simulation_byte,
    rmw_patch,
)
from grawji.recipe import Recipe

VELVIA_BYTE = 2
ACROS_BYTE = 12


class FakeCamera:
    """A stand-in for rawji.FujiCamera that records the call sequence."""

    def __init__(self, *, connect_ok=True, fail_on=None, profile=None):
        self.calls = []
        self._connect_ok = connect_ok
        self._fail_on = fail_on  # method name that should raise 0x2002
        self._profile = profile if profile is not None else bytes(600)
        self.set_profiles = []
        self.last_full_resolution = None

    def _maybe_fail(self, name):
        if self._fail_on == name:
            raise RuntimeError("PTP GetDevicePropValue failed: 0x2002")

    def connect(self):
        self.calls.append("connect")
        return self._connect_ok

    def send_raf(self, filepath):
        self.calls.append(("send_raf", filepath))
        self._maybe_fail("send_raf")

    def get_profile(self):
        self.calls.append("get_profile")
        self._maybe_fail("get_profile")
        return self._profile

    def set_profile(self, profile):
        self.calls.append("set_profile")
        self.set_profiles.append(profile)

    def trigger_conversion(self, full_resolution=True):
        self.calls.append(("trigger_conversion", full_resolution))
        self.last_full_resolution = full_resolution

    def wait_for_result(self, timeout=30):
        self.calls.append(("wait_for_result", timeout))
        return b"JPEG"

    def disconnect(self):
        self.calls.append("disconnect")


def session_for(camera):
    """A CameraSession whose factory always returns ``camera``."""
    return CameraSession(camera_factory=lambda: camera)


# --- rmw_patch -------------------------------------------------------------


def test_rmw_patch_sets_film_sim_byte():
    base = bytes(OFFSET_FILM_SIM + 10)
    patched = rmw_patch(base, film_sim_byte=VELVIA_BYTE)
    assert patched[OFFSET_FILM_SIM] == VELVIA_BYTE
    assert patched[:OFFSET_FILM_SIM] == base[:OFFSET_FILM_SIM]
    assert patched[OFFSET_FILM_SIM + 1 :] == base[OFFSET_FILM_SIM + 1 :]


def test_rmw_patch_does_not_mutate_input():
    base = bytes(OFFSET_FILM_SIM + 10)
    rmw_patch(base, film_sim_byte=ACROS_BYTE)
    assert base[OFFSET_FILM_SIM] == 0x00


def test_rmw_patch_rejects_short_profile():
    with pytest.raises(ValueError, match="too short"):
        rmw_patch(bytes(10), film_sim_byte=VELVIA_BYTE)


# --- film_simulation_byte / apply_recipe -----------------------------------


def test_film_simulation_byte_known():
    assert film_simulation_byte("Velvia") == VELVIA_BYTE
    assert film_simulation_byte("Acros") == ACROS_BYTE


def test_film_simulation_byte_unknown():
    with pytest.raises(ValueError, match="film simulation"):
        film_simulation_byte("Nope")


def test_apply_recipe_patches_film_sim():
    base = bytes(600)
    patched = apply_recipe(base, Recipe(film_simulation="Velvia"))
    assert patched[OFFSET_FILM_SIM] == VELVIA_BYTE


def test_apply_recipe_rejects_size_quality_until_wired():
    base = bytes(600)
    with pytest.raises(NotImplementedError, match="not wired up"):
        apply_recipe(base, Recipe(image_size="L"))
    with pytest.raises(NotImplementedError, match="not wired up"):
        apply_recipe(base, Recipe(quality="FINE"))


# --- CameraSession ---------------------------------------------------------


def test_open_call_order():
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
    cam = FakeCamera()
    session = session_for(cam)
    session.open("/tmp/shot.RAF")
    session.render(Recipe(), full_resolution=True)
    assert cam.last_full_resolution is True


def test_render_before_open_raises():
    session = session_for(FakeCamera())
    with pytest.raises(SessionStateError):
        session.render(Recipe(), full_resolution=False)


def test_connect_failure_raises_camera_error():
    session = session_for(FakeCamera(connect_ok=False))
    with pytest.raises(CameraError):
        session.open("/tmp/shot.RAF")
    assert not session.is_open


def test_foreign_raf_raises_and_cleans_up():
    cam = FakeCamera(fail_on="get_profile")
    session = session_for(cam)
    with pytest.raises(ForeignRafError):
        session.open("/tmp/foreign.RAF")
    assert not session.is_open
    assert "disconnect" in cam.calls  # half-open session cleaned up


def test_close_is_idempotent_and_disconnects():
    cam = FakeCamera()
    session = session_for(cam)
    session.open("/tmp/shot.RAF")
    session.close()
    session.close()  # second call must not raise
    assert not session.is_open
    assert cam.calls.count("disconnect") == 1


def test_context_manager_closes():
    cam = FakeCamera()
    with session_for(cam) as session:
        session.open("/tmp/shot.RAF")
    assert "disconnect" in cam.calls
