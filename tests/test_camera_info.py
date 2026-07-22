"""Tests for camera detection and camera-error classification."""

import usb.core

from grawji.camera_info import (
    detect_camera,
    is_camera_disconnected,
    is_camera_stuck,
)


class _FakeDevice:
    """Stand-in for a pyusb device carrying only the product id."""

    def __init__(self, pid):
        self.idProduct = pid


def test_detects_known_body(monkeypatch):
    """A known Fuji product id maps to its friendly name."""
    monkeypatch.setattr(usb.core, "find", lambda **_: _FakeDevice(0x0313))
    assert detect_camera() == "X-E5"


def test_unknown_pid_gets_generic_label(monkeypatch):
    """Any device on the Fuji vendor id is accepted, label is generic."""
    monkeypatch.setattr(usb.core, "find", lambda **_: _FakeDevice(0xFFFF))
    assert detect_camera() == "Camera"


def test_no_device_means_none(monkeypatch):
    """No device on the Fuji vendor id yields None."""
    monkeypatch.setattr(usb.core, "find", lambda **_: None)
    assert detect_camera() is None


def test_usb_error_means_none(monkeypatch):
    """A failing USB backend is reported as no camera, not an error."""

    def _raise(**_):
        raise usb.core.USBError("no backend available")

    monkeypatch.setattr(usb.core, "find", _raise)
    assert detect_camera() is None


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
