"""Per-body capabilities: which recipe features the camera supports.

The feature table below encodes docs/feature-matrix.md: every body with
USB RAW conversion, keyed by its EXIF model name. An unknown or missing
model falls back to the X-Pro2 baseline, the minimum feature set every
USB-capable body honours. The live profile can only narrow a table row,
never widen it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from rawji.fuji_profile import (
    PROFILE_PARAMS_OFFSET,
    is_xprocessor5,
    read_iopcode,
)

__all__ = [
    "BASELINE",
    "FILM_SIMULATIONS",
    "Capabilities",
    "capabilities_for",
    "is_known_model",
    "is_xprocessor5",
    "read_iopcode",
]

# High-index effect slots (513 + index * 4). They only exist on long enough
# profiles; older bodies (X100F 601 B, X-T3 605 B) stop before them.
_OFFSET_SMOOTH_SKIN = PROFILE_PARAMS_OFFSET + 23 * 4
_OFFSET_COLOR_CHROME_BLUE = PROFILE_PARAMS_OFFSET + 24 * 4
_OFFSET_CLARITY = PROFILE_PARAMS_OFFSET + 26 * 4

# Every film simulation grawji can write (grawji.core maps the names to
# profile codes, including the ones rawji's enum lacks), in the camera's
# own menu order.
FILM_SIMULATIONS = (
    "Provia",
    "Velvia",
    "Astia",
    "ClassicChrome",
    "RealaAce",
    "ProNegHi",
    "ProNegStd",
    "ClassicNeg",
    "NostalgicNeg",
    "Eterna",
    "EternaBleach",
    "Acros",
    "AcrosYe",
    "AcrosR",
    "AcrosG",
    "Monochrome",
    "MonochromeYe",
    "MonochromeR",
    "MonochromeG",
    "Sepia",
)


def _sims_without(*excluded: str) -> tuple[str, ...]:
    """The film-sim list minus the given names, keeping menu order."""
    return tuple(s for s in FILM_SIMULATIONS if s not in excluded)


_SIMS_GEN3 = _sims_without(
    "Eterna", "EternaBleach", "ClassicNeg", "NostalgicNeg", "RealaAce"
)
_SIMS_ETERNA = _sims_without(
    "EternaBleach", "ClassicNeg", "NostalgicNeg", "RealaAce"
)
_SIMS_CLASSIC_NEG = _sims_without("EternaBleach", "NostalgicNeg", "RealaAce")
_SIMS_BLEACH = _sims_without("NostalgicNeg", "RealaAce")
_SIMS_NO_REALA = _sims_without("RealaAce")
_SIMS_ALL = FILM_SIMULATIONS


@dataclass(frozen=True)
class Capabilities:
    """Recipe features one camera body supports.

    Attributes:
        tone_min: Lowest highlight/shadow tone the body honours.
        tone_max: Highest highlight/shadow tone the body honours.
        tone_half_step: Whether the body accepts 0.5 tone steps
            (hardware-verified on the XProcessor5 X-E5 only).
        has_grain_size: Whether grain size (Small/Large) is supported;
            it shares the grain slot at offset 545.
        has_color_chrome: Whether Color Chrome Effect (offset 549) works.
            The slot exists on every body but is inert before the X-T3.
        has_color_chrome_blue: Whether Color Chrome FX Blue (609) works.
        has_clarity: Whether Clarity (offset 617) works.
        has_smooth_skin: Whether Smooth Skin Effect (offset 605) works.
        film_simulations: The film simulations the body offers, from
            rawji's enum vocabulary.

    The field defaults are the X-Pro2 baseline.
    """

    tone_min: int = -2
    tone_max: int = 4
    tone_half_step: bool = False
    has_grain_size: bool = False
    has_color_chrome: bool = False
    has_color_chrome_blue: bool = False
    has_clarity: bool = False
    has_smooth_skin: bool = False
    film_simulations: tuple[str, ...] = _SIMS_GEN3


# The safe minimum used when the camera cannot be identified.
BASELINE = Capabilities()

# Feature tiers per docs/feature-matrix.md. The splits do not follow
# processor generations cleanly, which is why the table is per body, not per
# processor.
_GEN4_EARLY = Capabilities(
    has_color_chrome=True, film_simulations=_SIMS_ETERNA
)
_GEN4_LATE = Capabilities(
    has_grain_size=True,
    has_color_chrome=True,
    has_color_chrome_blue=True,
    has_clarity=True,
    film_simulations=_SIMS_CLASSIC_NEG,
)
_GEN4_BLEACH = replace(_GEN4_LATE, film_simulations=_SIMS_BLEACH)
_GEN5 = replace(
    _GEN4_BLEACH,
    has_smooth_skin=True,
    tone_half_step=True,
    film_simulations=_SIMS_NO_REALA,
)
_GEN5_REALA = replace(_GEN5, film_simulations=_SIMS_ALL)
_GFX_PRO = Capabilities(has_color_chrome=True, has_smooth_skin=True)
_GFX_GEN4 = replace(
    _GEN4_BLEACH, has_smooth_skin=True, film_simulations=_SIMS_NO_REALA
)

# EXIF model name (normalized by _normalize) -> feature tier.
_MODEL_CAPABILITIES = {
    "XPRO2": BASELINE,
    "XT2": BASELINE,
    "X100F": BASELINE,
    "XT20": BASELINE,
    "XE3": BASELINE,
    "XH1": Capabilities(film_simulations=_SIMS_ETERNA),
    "XT3": _GEN4_EARLY,
    "XT30": _GEN4_EARLY,
    "XPRO3": _GEN4_LATE,
    "X100V": _GEN4_LATE,
    "XT4": _GEN4_BLEACH,
    "XS10": _GEN4_BLEACH,
    "XE4": _GEN4_BLEACH,
    "XT30II": _GEN4_BLEACH,
    "XH2S": _GEN5,
    "XH2": _GEN5,
    "XT5": _GEN5,
    "XS20": _GEN5,
    "X100VI": _GEN5_REALA,
    "XT50": _GEN5_REALA,
    "XM5": _GEN5_REALA,
    "XE5": _GEN5_REALA,
    "XT30III": _GEN5_REALA,
    "GFX50S": _GFX_PRO,
    "GFX50R": _GFX_PRO,
    "GFX100": _GFX_GEN4,
    "GFX100S": _GFX_GEN4,
    "GFX50SII": _GFX_GEN4,
    "GFX100II": _GEN5_REALA,
    "GFX100SII": _GEN5_REALA,
    "GFX100RF": _GEN5_REALA,
}


def _normalize(model: str) -> str:
    """Reduce an EXIF model string to a table key."""
    return "".join(
        ch for ch in model.upper().replace("FUJIFILM", "") if ch.isalnum()
    )


def is_known_model(model: str | None) -> bool:
    """Whether the model has a row in the capability table.

    False means capabilities_for falls back to the conservative
    baseline. Either the model tag was unreadable or the body is
    newer than the table.
    """
    return model is not None and _normalize(model) in _MODEL_CAPABILITIES


def capabilities_for(profile: bytes, model: str | None = None) -> Capabilities:
    """Return the capabilities for the identified body."""
    caps = _MODEL_CAPABILITIES.get(_normalize(model)) if model else None
    if caps is None:
        caps = BASELINE
    iopcode = read_iopcode(profile)
    xproc5 = iopcode is not None and is_xprocessor5(iopcode)
    size = len(profile)
    return replace(
        caps,
        tone_half_step=caps.tone_half_step and xproc5,
        has_smooth_skin=(
            caps.has_smooth_skin and size >= _OFFSET_SMOOTH_SKIN + 4
        ),
        has_color_chrome_blue=(
            caps.has_color_chrome_blue
            and size >= _OFFSET_COLOR_CHROME_BLUE + 4
        ),
        has_clarity=caps.has_clarity and size >= _OFFSET_CLARITY + 4,
    )
