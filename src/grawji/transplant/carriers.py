"""Pick a carrier and the DR compensation for a donor's shot DR.

The carrier's SHOT dynamic range bakes an exposure push into every
render, independent of the recipe's DR slot (hardware-proven on the X-T3):
a DR100 donor through a DR400 carrier comes out ~2 stops bright,
a DR400 donor through a DR100 carrier ~2 stops dark, and
a recipe DR above the carrier's shot DR renders green garbage.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

DR_CLASSES = (100, 200, 400)


@dataclass(frozen=True)
class CarrierChoice(Generic[T]):
    """The selected carrier and the DR-mismatch compensation.

    Attributes:
        carrier: The chosen entry from the available mapping.
        dynamic_range: The chosen carrier's shot DR.
        exposure_scale: Raw-domain factor for embed.build_transplant's
            exposure_scale.
        ev_offset: EV to add via the profile slot.
    """

    carrier: T
    dynamic_range: int
    exposure_scale: float
    ev_offset: float


def _stops(dynamic_range: int) -> float:
    """The DR class as stops above DR100."""
    if dynamic_range not in DR_CLASSES:
        raise ValueError(f"unknown dynamic range {dynamic_range}")
    return math.log2(dynamic_range / 100)


def select_carrier(
    donor_dr: int, available: Mapping[int, T]
) -> CarrierChoice[T]:
    """Choose the carrier and compensation for a donor's shot DR."""
    donor_stops = _stops(donor_dr)
    for dr in available:
        _stops(dr)
    if not available:
        raise ValueError("no carrier available")
    if donor_dr in available:
        return CarrierChoice(available[donor_dr], donor_dr, 1.0, 0.0)
    higher = [dr for dr in available if dr > donor_dr]
    if higher:
        best = min(higher)
        return CarrierChoice(
            carrier=available[best],
            dynamic_range=best,
            exposure_scale=donor_dr / best,
            ev_offset=0.0,
        )
    best = max(available)
    return CarrierChoice(
        carrier=available[best],
        dynamic_range=best,
        exposure_scale=1.0,
        ev_offset=donor_stops - _stops(best),
    )
