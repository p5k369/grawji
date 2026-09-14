"""Camera presence detection and camera-error classification."""

from __future__ import annotations

from pathlib import Path

import usb.core
from rawji.fuji_enums import FUJIFILM_USB_VENDOR_ID, PTPResponseCode

# Codes rawji names come from its enum.
# DEVICE_BUSY (0x2019) is standard PTP but absent from that enum.
_DEVICE_BUSY = 0x2019


def _ptp_code_in(exc: Exception, code: int) -> bool:
    """Whether exc's message carries the given PTP response code."""
    return f"0x{code:04X}".casefold() in str(exc).casefold()


# Friendly names for known Fuji product ids.
# No public id is known yet for the X-T30 II/III, X-T50, GFX 50S II,
# GFX100S II and GFX100RF.
PID_NAMES = {
    0x02CB: "X-Pro2",
    0x02CD: "X-T2",
    0x02D1: "X100F",
    0x02D3: "GFX 50S",
    0x02D4: "X-T20",
    0x02D6: "X-E3",
    0x02D7: "X-H1",
    0x02DC: "GFX 50R",
    0x02DD: "X-T3",
    0x02DE: "GFX100",
    0x02E3: "X-T30",
    0x02E4: "X-Pro3",
    0x02E5: "X100V",
    # rawji's enum says 0x02E7, which no source anywhere backs.
    0x02E6: "X-T4",
    0x02E8: "X-E4",
    0x02E9: "GFX 100S",
    0x02EA: "X-S10",
    0x02F0: "X-H2S",
    0x02F2: "X-H2",
    0x02F6: "X-S20",
    0x02F7: "X-S20",
    0x02FC: "X-T5",
    0x02FE: "GFX100 II",
    0x0305: "X100VI",
    0x030C: "X-M5",
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
    return isinstance(exc, TimeoutError) or _ptp_code_in(exc, _DEVICE_BUSY)


def is_camera_disconnected(exc: Exception) -> bool:
    """Whether exc signals the camera was unplugged (or gone from USB).

    A write to a vanished device fails with errno 19 (No such device);
    a fresh connect that finds nothing raises "could not connect".
    """
    text = str(exc)
    return "No such device" in text or "could not connect" in text


def is_foreign_raf(exc: Exception) -> bool:
    """Whether exc signals a RAF shot by a different camera body."""
    return _ptp_code_in(exc, PTPResponseCode.GeneralError)
