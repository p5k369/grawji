"""Drive the camera through a transplant conversion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from grawji.camera import core
from grawji.recipe import Recipe
from grawji.transplant.embed import Transplant
from grawji.transplant.lens import LensTables
from grawji.transplant.output import finish_jpeg
from grawji.transplant.profile_patch import patch_ev, patch_profile

DEFAULT_TIMEOUT = 300


class TransplantCamera(Protocol):
    """The subset of the rawji camera object the pipeline drives."""

    def send_raf(self, filepath: str) -> object:
        """Upload a RAF for conversion."""
        ...

    def get_profile(self) -> bytes:
        """Read the d185 profile of the uploaded RAF."""
        ...

    def set_profile(self, profile: bytes) -> object:
        """Write the patched d185 profile back."""
        ...

    def trigger_conversion(self, full_resolution: bool = ...) -> object:
        """Start the conversion."""
        ...

    def wait_for_result(
        self, timeout: int = ..., thumbnail: bool = ...
    ) -> bytes:
        """Poll for the finished render and download it."""
        ...


@dataclass(frozen=True)
class RenderOptions:
    """Recipe and job settings for a transplant conversion.

    Attributes:
        recipe: Full recipe applied.
        ev: DR-mismatch compensation in EV.
        film_sim: rawji FilmSimulation name applied to the render.
        wb: rawji WhiteBalance name written when neutralizing.
        neutralize: Reset the carrier's bleed-through settings.
        wb_shift: White-balance shift (R, B) for residual tint.
        donor_lens: The donor lens's tables.
        full_resolution: Full-resolution export instead of a preview.
        timeout: Seconds to wait for the conversion.
    """

    recipe: Recipe | None = None
    ev: float = 0.0
    film_sim: str | None = None
    wb: str = "Daylight"
    neutralize: bool = True
    wb_shift: tuple[int, int] = (0, 0)
    donor_lens: LensTables | None = None
    full_resolution: bool = True
    timeout: int = DEFAULT_TIMEOUT


def develop(
    camera: TransplantCamera,
    transplant: Transplant,
    workdir: Path,
    options: RenderOptions | None = None,
) -> bytes:
    """Convert the transplant on the connected body."""
    opts = options or RenderOptions()
    raf_path = workdir / "transplant.RAF"
    raf_path.write_bytes(transplant.raf)
    try:
        camera.send_raf(str(raf_path))
        base = camera.get_profile()
        if opts.recipe is not None:
            profile = core.apply_recipe(base, opts.recipe)
        else:
            profile = patch_profile(
                base,
                film_sim=opts.film_sim,
                wb=opts.wb,
                neutralize=opts.neutralize,
                wb_shift=opts.wb_shift,
            )
        if opts.ev:
            exposure = opts.recipe.exposure if opts.recipe is not None else 0.0
            profile = patch_ev(profile, exposure + opts.ev)
        camera.set_profile(profile)
        camera.trigger_conversion(full_resolution=opts.full_resolution)
        jpeg = camera.wait_for_result(opts.timeout)
    finally:
        raf_path.unlink(missing_ok=True)
    return finish_jpeg(
        jpeg,
        flip=transplant.flip,
        donor_size=transplant.donor_size,
        carrier_size=transplant.carrier_size,
        offset=transplant.offset,
        donor_lens=opts.donor_lens,
        compensate=opts.full_resolution,
    )
