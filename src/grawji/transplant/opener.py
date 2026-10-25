"""Prepare a foreign RAF for the connected body's engine."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
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
    carrier: Path,
    carrier_model: str,
    *,
    micro_contrast: bool = True,
) -> Prepared:
    """Embed the donor into the carrier and write the file to upload."""
    donor_lens = lens.lens_tables(donor)
    carrier_lens = lens.lens_tables(carrier)
    if carrier_lens is not None and carrier_lens.corrects:
        msg = (
            f"the carrier {carrier.name} was shot through a lens; use a "
            "carrier shot without one"
        )
        raise TransplantError(msg)
    choice = carriers.select_carrier(
        dynamic_range_of(donor), {dynamic_range_of(carrier): carrier}
    )
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
    try:
        frame = embed.decode(Path(path))
        embed.locate_pixel_base(Path(path).read_bytes(), frame.pixels)
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
        carrier = carrier_for(current, model)
        if carrier is None:
            return None
        return prepare(
            donor,
            carrier,
            model,
            micro_contrast=current.crossbody_micro_contrast,
        )

    return open_foreign


def carrier_for(settings: Settings, model: str) -> Path | None:
    """The registered carrier of a body, by its normalized model name."""
    wanted = normalize_model(model)
    for registered, path in settings.carriers.items():
        if normalize_model(registered) == wanted and path:
            return Path(path)
    return None


def normalize_model(model: str) -> str:
    """A model name as a comparison key."""
    return "".join(
        ch for ch in model.upper().replace("FUJIFILM", "") if ch.isalnum()
    )
