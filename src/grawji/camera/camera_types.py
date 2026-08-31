"""Shared types for the camera transfer modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Camera(Protocol):
    """The subset of the rawji camera object the transfer modules use."""

    def send_command(
        self, code: int, params: list[int] | None = ...
    ) -> tuple[int, list[int], bytes]:
        """Issue a PTP command."""
        ...

    def send_data_command(
        self, code: int, params: list[int], data: bytes
    ) -> tuple[int, list[int]]:
        """Issue a PTP command with a data phase."""
        ...


class BackupTransferError(RuntimeError):
    """A backup transfer failed (unsupported body, PTP error, or no-op)."""


@dataclass(frozen=True)
class TransferResult:
    """Outcome of a recipe transfer.

    Attributes:
        model: The body model parsed from the blob or DeviceInfo.
        slots: The bank indices written.
        applied: Count of intended recipe values confirmed on the camera.
        maintained: Offsets the camera rewrote itself (checksum, counters).
        dropped: Recipe features the body could not store, per slot.
    """

    model: str
    slots: tuple[int, ...]
    applied: int
    maintained: tuple[int, ...]
    dropped: dict[int, list[str]]
