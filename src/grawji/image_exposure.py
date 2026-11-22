"""The exposure compensation of the open image."""

from __future__ import annotations

# EVs are thirds of a stop, anything closer than this is the same EV.
_EV_EPSILON = 1e-6


class ImageExposure:
    """Tracks one image's exposure against what is stored for it.

    Attributes:
        stored: The exposure stored beside the image.
        current: The exposure last taken for the image.
        shot: The exposure the image was shot with
    """

    def __init__(self) -> None:
        """Start without an image."""
        self.stored: float | None = None
        self.current: float | None = None
        self.shot: float | None = None

    def begin(self, stored: float | None) -> None:
        """Start a new image with what is stored for it."""
        self.stored = stored
        self.current = None
        self.shot = None

    def opened(self, shot: float, current: float) -> None:
        """Take the shot's exposure and the one the image starts with."""
        self.shot = shot
        self.current = current

    def settle(self, exposure: float) -> tuple[bool, float | None]:
        """Take the exposure the controls ended on."""
        if self.current is not None and exposure == self.current:
            return False, None
        self.current = exposure
        back_to_shot = (
            self.shot is not None and abs(exposure - self.shot) < _EV_EPSILON
        )
        return True, None if back_to_shot else exposure
