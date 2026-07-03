"""Tests for camera-error classification."""

from grawji.camera_info import is_camera_disconnected, is_camera_stuck


def test_stuck_on_timeout():
    """A conversion that never returned means a wedged body."""
    assert is_camera_stuck(TimeoutError()) is True


def test_stuck_on_device_busy():
    """PTP 0x2019 (Device_Busy) after a conversion means a wedged body."""
    assert is_camera_stuck(RuntimeError("PTP error 0x2019")) is True


def test_not_stuck_on_other_errors():
    """Ordinary failures are not misread as a hung camera."""
    assert is_camera_stuck(RuntimeError("PTP error 0x2002")) is False


def test_disconnected_on_vanished_device():
    """Errno 19 from a USB write means the camera is gone."""
    assert is_camera_disconnected(OSError("[Errno 19] No such device")) is True


def test_disconnected_on_failed_connect():
    """A fresh connect that finds nothing means no camera."""
    assert is_camera_disconnected(RuntimeError("could not connect")) is True


def test_not_disconnected_on_other_errors():
    """Ordinary failures are not misread as an unplugged camera."""
    assert is_camera_disconnected(RuntimeError("conversion failed")) is False
