"""Camera presence detection and camera-error classification."""

from __future__ import annotations

from pathlib import Path

import usb.core
from rawji.fuji_enums import FUJIFILM_USB_VENDOR_ID

# Friendly names for known Fuji product ids. Detection accepts any device on
# the Fuji vendor id. This map only supplies a nice label.
# todo: This map is cosmetic only, could be very well integrated in rawji.
PID_NAMES = {
    0x02D1: "X100F",
    0x02DD: "X-T3",
    0x02E3: "X-T30",
    0x02E5: "X100V",
    0x02E7: "X-T4",
    0x0313: "X-E5",
}

# Whether we run inside a Flatpak sandbox, which cannot see a camera that is
# unplugged and plugged back in until the app restarts.
IN_FLATPAK = Path("/.flatpak-info").exists()


def detect_camera() -> str | None:
    """Return the connected camera's label, or None if none is found.

    Enumeration only. It never opens or claims the device, so it
    is safe to poll alongside an active camera session.
    """
    try:
        device = usb.core.find(idVendor=FUJIFILM_USB_VENDOR_ID)
    except (usb.core.USBError, OSError, ValueError):
        return None  # e.g. no libusb backend available
    if device is None:
        return None
    return PID_NAMES.get(device.idProduct, "Camera")


def is_camera_stuck(exc: Exception) -> bool:
    """Whether exc signals a hung camera (timed-out or busy conversion).

    A conversion that never returns (TimeoutError) or a follow-up call
    rejected with PTP 0x2019 (Device_Busy) both mean the body is wedged
    and needs a power cycle - retrying from here cannot recover it.
    """
    return isinstance(exc, TimeoutError) or "0x2019" in str(exc)


def is_camera_disconnected(exc: Exception) -> bool:
    """Whether exc signals the camera was unplugged (or gone from USB).

    A write to a vanished device fails with errno 19 (No such device);
    a fresh connect that finds nothing raises "could not connect".
    """
    text = str(exc)
    return "No such device" in text or "could not connect" in text
