"""Tests for preparing a foreign RAF for the connected body's engine."""

from pathlib import Path
from typing import cast

import pytest

from grawji.settings import Settings
from grawji.transplant import embed, lens, opener
from grawji.transplant.errors import TransplantError

_KNOTS = (0.5, 1.0)
_LENSED = lens.LensTables(_KNOTS, (-1.0, -3.0), (90.0, 60.0), (0, 0), (0, 0))
_LENSLESS = lens.LensTables(_KNOTS, (0, 0), (100.0, 100.0), (0, 0), (0, 0))
_DECODED = cast("embed.DecodedCarrier", ("frame", 0))


def _fake_transplant(raf: bytes = b"carrier-with-donor") -> embed.Transplant:
    return embed.Transplant(
        raf=raf, flip=0, donor_size=(4, 6), carrier_size=(8, 12), offset=(0, 0)
    )


def _stub(monkeypatch, *, tables, dynamic_ranges, transplant=None):
    """Stub the metadata reads and the embedding with canned values."""
    built = {}

    def build(
        donor,
        carrier,
        *,
        exposure_scale=1.0,
        donor_lens=None,
        carrier_decoded=None,
    ):
        built.update(
            donor=donor,
            carrier=carrier,
            exposure_scale=exposure_scale,
            donor_lens=donor_lens,
        )
        return transplant or _fake_transplant()

    monkeypatch.setattr(opener.lens, "lens_tables", lambda p: tables[Path(p)])
    monkeypatch.setattr(
        opener.imagemeta,
        "fuji_dynamic_range",
        lambda p: dynamic_ranges[Path(p)],
    )
    monkeypatch.setattr(opener.embed, "build_transplant", build)
    monkeypatch.setattr(opener, "decoded_carrier", lambda path: _DECODED)
    return built


def test_prepare_writes_the_stand_in_and_keeps_the_donor_tables(
    monkeypatch, tmp_path
):
    """The donor's lens tables ride along, the carrier file is written."""
    donor, carrier = tmp_path / "donor.RAF", tmp_path / "carrier.RAF"
    built = _stub(
        monkeypatch,
        tables={donor: _LENSED, carrier: _LENSLESS},
        dynamic_ranges={donor: 100, carrier: 100},
    )

    prepared = opener.prepare(donor, {100: carrier}, "X-E5")

    assert prepared.raf_path.read_bytes() == b"carrier-with-donor"
    assert prepared.donor_lens is _LENSED
    assert prepared.ev_offset == 0.0
    assert prepared.carrier_model == "X-E5"
    assert built["donor_lens"] is _LENSED
    assert built["exposure_scale"] == 1.0
    prepared.cleanup()
    assert not prepared.raf_path.exists()


def test_prepare_scales_a_lower_dr_donor_into_a_higher_carrier(
    monkeypatch, tmp_path
):
    """A DR100 donor on a DR400 carrier is exposed down in the raw."""
    donor, carrier = tmp_path / "donor.RAF", tmp_path / "carrier.RAF"
    built = _stub(
        monkeypatch,
        tables={donor: None, carrier: None},
        dynamic_ranges={donor: 100, carrier: 400},
    )

    prepared = opener.prepare(donor, {400: carrier}, "X-E5")

    assert built["exposure_scale"] == pytest.approx(0.25)
    assert prepared.ev_offset == 0.0
    prepared.cleanup()


def test_prepare_falls_back_to_ev_for_a_higher_dr_donor(monkeypatch, tmp_path):
    """A DR400 donor on a DR100 carrier gets its two stops as EV."""
    donor, carrier = tmp_path / "donor.RAF", tmp_path / "carrier.RAF"
    _stub(
        monkeypatch,
        tables={donor: None, carrier: None},
        dynamic_ranges={donor: 400, carrier: None},
    )

    prepared = opener.prepare(donor, {100: carrier}, "X-E5")

    assert prepared.ev_offset == pytest.approx(2.0)
    prepared.cleanup()


def test_prepare_refuses_a_lensed_carrier(monkeypatch, tmp_path):
    """A carrier shot through a lens is refused before anything is built."""
    donor, carrier = tmp_path / "donor.RAF", tmp_path / "carrier.RAF"
    _stub(
        monkeypatch,
        tables={donor: _LENSED, carrier: _LENSED},
        dynamic_ranges={donor: 100, carrier: 100},
    )
    with pytest.raises(TransplantError, match="without one"):
        opener.prepare(donor, {100: carrier}, "X-E5")


def test_prepare_picks_the_carrier_of_the_donors_dynamic_range(
    monkeypatch, tmp_path
):
    """With a set of carriers the donor's own DR class is used as is."""
    donor = tmp_path / "donor.RAF"
    low, mid, high = (tmp_path / f"dr{dr}.RAF" for dr in (100, 200, 400))
    built = _stub(
        monkeypatch,
        tables={donor: None, low: None, mid: None, high: None},
        dynamic_ranges={donor: 200, low: 100, mid: 200, high: 400},
    )

    prepared = opener.prepare(donor, {100: low, 400: high, 200: mid}, "X-E5")

    assert built["carrier"] == mid
    assert built["exposure_scale"] == 1.0
    assert prepared.ev_offset == 0.0
    prepared.cleanup()


def test_finish_passes_the_geometry_and_the_preview_flags(monkeypatch):
    """The crop uses the transplant geometry; previews skip the MTF."""
    seen = {}

    def fake_finish(jpeg, **kwargs):
        seen.update(kwargs)
        return b"done"

    monkeypatch.setattr(opener.output, "finish_jpeg", fake_finish)
    prepared = opener.Prepared(
        Path("x.RAF"), _fake_transplant(), _LENSED, 0.0, "X-E5"
    )

    assert prepared.finish(b"jpeg", full_resolution=False) == b"done"
    assert seen["donor_size"] == (4, 6)
    assert seen["carrier_size"] == (8, 12)
    assert seen["donor_lens"] is _LENSED
    assert seen["compensate"] is False
    assert seen["letterboxed"] is True
    assert seen["inset"] is True

    prepared.finish(b"jpeg", full_resolution=True)
    assert seen["compensate"] is True
    assert seen["letterboxed"] is False

    padded = opener.Prepared(
        Path("x.RAF"),
        embed.Transplant(
            raf=b"",
            flip=0,
            donor_size=(4, 6),
            carrier_size=(8, 12),
            offset=(1, 1),
            context=6,
        ),
        _LENSED,
        0.0,
        "X-E5",
    )
    padded.finish(b"jpeg", full_resolution=True)
    assert seen["inset"] is False
    padded.finish(b"jpeg", full_resolution=False)
    assert seen["inset"] is True

    whole = opener.Prepared(
        Path("x.RAF"),
        embed.Transplant(
            raf=b"",
            flip=0,
            donor_size=(8, 12),
            carrier_size=(8, 12),
            offset=(0, 0),
            whole=True,
        ),
        _LENSED,
        0.0,
        "X-E5",
    )
    whole.finish(b"jpeg", full_resolution=True)
    assert seen["inset"] is False

    muted = opener.Prepared(
        Path("x.RAF"),
        _fake_transplant(),
        _LENSED,
        0.0,
        "X-E5",
        micro_contrast=False,
    )
    muted.finish(b"jpeg", full_resolution=True)
    assert seen["compensate"] is False


def test_carriers_for_matches_models_loosely():
    """FUJIFILM X-E5, x-e5 and X-E5 name the same carriers."""
    settings = Settings(
        carriers={"FUJIFILM X-E5": {"400": "/carriers/xe5.RAF", "100": ""}}
    )
    assert opener.carriers_for(settings, "x-e5") == {
        400: Path("/carriers/xe5.RAF")
    }
    assert opener.carriers_for(settings, "X-T3") == {}


def test_carriers_for_classes_an_old_carrier_by_its_metadata(monkeypatch):
    """A carrier from before the ranges were recorded reads its own DR."""
    monkeypatch.setattr(opener.imagemeta, "fuji_dynamic_range", lambda p: 400)
    settings = Settings(carriers={"X-E5": {"": "/carriers/old.RAF"}})
    assert opener.carriers_for(settings, "X-E5") == {
        400: Path("/carriers/old.RAF")
    }


def test_from_settings_reads_the_settings_on_every_call(monkeypatch, tmp_path):
    """A carrier registered after the hook was made still counts."""
    settings = Settings()
    calls = []
    monkeypatch.setattr(
        opener,
        "prepare",
        lambda donor, available, model, *, micro_contrast: calls.append(
            (donor, available, model, micro_contrast)
        ),
    )
    hook = opener.from_settings(lambda: settings)
    donor = tmp_path / "donor.RAF"

    assert hook(donor, "X-E5") is None
    settings.carriers["X-E5"] = {"400": "/carriers/xe5.RAF"}
    settings.crossbody_micro_contrast = False
    hook(donor, "X-E5")
    assert calls == [(donor, {400: Path("/carriers/xe5.RAF")}, "X-E5", False)]


def test_check_carrier_names_what_is_wrong(monkeypatch, tmp_path):
    """Wrong body, a lens, compression: each is told apart."""
    path = tmp_path / "carrier.RAF"
    path.write_bytes(b"raf")
    monkeypatch.setattr(opener.raf, "header_model", lambda p: "X-T3")
    assert "X-T3" in (opener.check_carrier(path, "X-E5") or "")
    monkeypatch.setattr(opener.raf, "header_model", lambda p: "X-E5")
    monkeypatch.setattr(opener.lens, "lens_tables", lambda p: _LENSED)
    assert "lens" in (opener.check_carrier(path, "X-E5") or "")
    monkeypatch.setattr(opener.lens, "lens_tables", lambda p: _LENSLESS)

    def compressed(p):
        raise TransplantError("could not locate the carrier pixel array")

    monkeypatch.setattr(opener.embed, "decode", compressed)
    assert "compressed" in (opener.check_carrier(path, "X-E5") or "")


def test_check_carrier_accepts_a_lensless_uncompressed_native_raf(
    monkeypatch, tmp_path
):
    """The happy path answers None."""
    path = tmp_path / "carrier.RAF"
    path.write_bytes(b"raf")
    monkeypatch.setattr(opener.raf, "header_model", lambda p: "FUJIFILM X-E5")
    monkeypatch.setattr(opener.lens, "lens_tables", lambda p: None)
    monkeypatch.setattr(
        opener.embed, "decode", lambda p: type("F", (), {"pixels": None})()
    )
    monkeypatch.setattr(opener.embed, "locate_pixel_base", lambda blob, px: 0)
    assert opener.check_carrier(path, "X-E5") is None


def test_the_carrier_is_decoded_once_per_file(monkeypatch, tmp_path):
    """Two foreign RAFs in a row share the decoded carrier."""
    carrier = tmp_path / "carrier.RAF"
    carrier.write_bytes(b"carrier")
    decodes: list[Path] = []

    def decode_carrier(path: Path) -> embed.DecodedCarrier:
        decodes.append(path)
        return _DECODED

    monkeypatch.setattr(opener.embed, "decode_carrier", decode_carrier)
    opener._carrier_cache.clear()
    assert opener.decoded_carrier(carrier) is _DECODED
    assert opener.decoded_carrier(carrier) is _DECODED
    assert decodes == [carrier]
    carrier.write_bytes(b"carrier, re-shot")
    opener.decoded_carrier(carrier)
    assert decodes == [carrier, carrier]


def test_a_full_render_carries_the_donor_exif(monkeypatch, tmp_path):
    """The donor's EXIF goes onto a full-size render, not onto previews."""
    monkeypatch.setattr(opener.output, "finish_jpeg", lambda jpeg, **k: b"PIC")
    monkeypatch.setattr(opener.output, "jpeg_size", lambda jpeg: (30, 20))
    monkeypatch.setattr(opener.raf, "embedded_jpeg", lambda path: b"SRC")
    seen = {}

    def with_exif_of(jpeg, source, size=None):
        seen.update(jpeg=jpeg, source=source, size=size)
        return b"PIC+EXIF"

    monkeypatch.setattr(opener.imagemeta, "with_exif_of", with_exif_of)
    prepared = opener.Prepared(
        Path("x.RAF"),
        _fake_transplant(),
        None,
        0.0,
        "X-E5",
        donor=tmp_path / "d.RAF",
    )
    assert prepared.finish(b"jpeg", full_resolution=False) == b"PIC"
    assert seen == {}
    assert prepared.finish(b"jpeg", full_resolution=True) == b"PIC+EXIF"
    assert seen == {"jpeg": b"PIC", "source": b"SRC", "size": (30, 20)}
