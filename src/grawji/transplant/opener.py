"""Prepare a foreign RAF for the connected body's engine."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from grawji import raf
from grawji.imaging import imagemeta
from grawji.settings import Settings
from grawji.transplant import carriers, embed, lens, output
from grawji.transplant.errors import TransplantError

# A DR the metadata does not state is taken as the base class.
_DEFAULT_DR = 100
# The last carrier decoded, keyed by its path, size and mtime
_carrier_cache: dict[tuple[str, int, int], embed.DecodedCarrier] = {}


@dataclass(frozen=True)
class Prepared:
    """A transplanted RAF, ready for the camera.

    Attributes:
        raf_path: The temporary carrier file to upload in the donor's place.
        transplant: The embedding geometry, for the crop after render.
        donor_lens: The donor lens's tables.
        ev_offset: EV to add to every recipe.
        carrier_model: The body that will develop the picture.
        micro_contrast: Whether a full-size render gets back the micro
            contrast its resampling took away.
        donor: The donor RAF, whose metadata a full-size render carries.
    """

    raf_path: Path
    transplant: embed.Transplant
    donor_lens: lens.LensTables | None
    ev_offset: float
    carrier_model: str
    micro_contrast: bool = True
    donor: Path | None = None

    def finish(self, jpeg: bytes, *, full_resolution: bool) -> bytes:
        """A render as the donor's picture: cropped, corrected, upright."""
        finished = output.finish_jpeg(
            jpeg,
            flip=self.transplant.flip,
            donor_size=self.transplant.donor_size,
            carrier_size=self.transplant.carrier_size,
            offset=self.transplant.offset,
            donor_lens=self.donor_lens,
            compensate=full_resolution and self.micro_contrast,
            letterboxed=not full_resolution,
            inset=not full_resolution or not self.transplant.seamless,
        )
        if not full_resolution or self.donor is None:
            return finished
        try:
            source = raf.embedded_jpeg(self.donor)
        except (OSError, ValueError):
            return finished
        return imagemeta.with_exif_of(
            finished, source, size=output.jpeg_size(finished)
        )

    def cleanup(self) -> None:
        """Remove the temporary carrier file."""
        self.raf_path.unlink(missing_ok=True)


def dynamic_range_of(path: Path | str) -> int:
    """The DR class a RAF was shot at."""
    value = imagemeta.fuji_dynamic_range(str(path))
    return value if value in carriers.DR_CLASSES else _DEFAULT_DR


def prepare(
    donor: Path,
    available: Mapping[int, Path],
    carrier_model: str,
    *,
    micro_contrast: bool = True,
) -> Prepared:
    """Embed the donor into the fitting carrier and write the upload file.

    Args:
        donor: The foreign RAF.
        available: The body's carriers by the dynamic range they were shot at.
        carrier_model: The body that develops the picture.
        micro_contrast: Whether a full-size render gets its micro
            contrast back.
    """
    choice = carriers.select_carrier(dynamic_range_of(donor), available)
    carrier = choice.carrier
    donor_lens = lens.lens_tables(donor)
    carrier_lens = lens.lens_tables(carrier)
    if carrier_lens is not None and carrier_lens.corrects:
        msg = (
            f"the carrier {carrier.name} was shot through a lens; use a "
            "carrier shot without one"
        )
        raise TransplantError(msg)
    transplant = embed.build_transplant(
        donor,
        carrier,
        exposure_scale=choice.exposure_scale,
        donor_lens=donor_lens,
        carrier_decoded=decoded_carrier(carrier),
    )
    with tempfile.NamedTemporaryFile(
        prefix="grawji-transplant-", suffix=".RAF", delete=False
    ) as handle:
        handle.write(transplant.raf)
        raf_path = Path(handle.name)
    return Prepared(
        raf_path=raf_path,
        transplant=transplant,
        donor_lens=donor_lens,
        ev_offset=choice.ev_offset,
        carrier_model=carrier_model,
        micro_contrast=micro_contrast,
        donor=donor,
    )


def check_carrier(path: Path | str, model: str) -> str | None:
    """Why a file cannot serve as the body's carrier."""
    named = raf.header_model(path)
    if named is None:
        return "This is not a Fujifilm RAF."
    if normalize_model(named) != normalize_model(model):
        return f"This RAF was shot by a {named}, not by the {model}."
    try:
        tables = lens.lens_tables(path)
    except TransplantError:
        tables = None
    if tables is not None and tables.corrects:
        return (
            "This RAF was shot through a lens; shoot the carrier without one."
        )
    return _pixel_problem(Path(path))


def _pixel_problem(path: Path) -> str | None:
    """Why a native RAF's pixel block cannot take a transplant."""
    try:
        frame = embed.decode(path)
        embed.locate_pixel_base(path.read_bytes(), frame.pixels)
    except TransplantError:
        return "This RAF is compressed. Shoot the carrier uncompressed."
    except (OSError, ValueError):
        return "This RAF could not be read."
    return None


def decoded_carrier(carrier: Path) -> embed.DecodedCarrier:
    """The carrier decoded, from the cache when the file is unchanged."""
    stat = carrier.stat()
    key = (str(carrier), stat.st_size, stat.st_mtime_ns)
    hit = _carrier_cache.get(key)
    if hit is None:
        hit = embed.decode_carrier(carrier)
        _carrier_cache.clear()
        _carrier_cache[key] = hit
    return hit


def from_settings(
    settings: Callable[[], Settings],
) -> Callable[[Path, str], Prepared | None]:
    """A session hook that prepares foreign RAFs with the user's carriers."""

    def open_foreign(donor: Path, model: str) -> Prepared | None:
        current = settings()
        available = carriers_for(current, model)
        if not available:
            return None
        return prepare(
            donor,
            available,
            model,
            micro_contrast=current.crossbody_micro_contrast,
        )

    return open_foreign


def carriers_for(settings: Settings, model: str) -> dict[int, Path]:
    """The registered carriers of a body by the dynamic range they hold."""
    wanted = normalize_model(model)
    found: dict[int, Path] = {}
    for registered, by_range in settings.carriers.items():
        if normalize_model(registered) != wanted:
            continue
        for key, path in by_range.items():
            if not path:
                continue
            shot = int(key) if key.isdigit() else dynamic_range_of(path)
            if shot in carriers.DR_CLASSES:
                found[shot] = Path(path)
    return found


def normalize_model(model: str) -> str:
    """A model name as a comparison key."""
    return "".join(
        ch for ch in model.upper().replace("FUJIFILM", "") if ch.isalnum()
    )
