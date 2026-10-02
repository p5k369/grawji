"""The catalog model tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grawji import catalog
from grawji.catalog import Entry, Filter, Order, Rejects
from grawji.marks import Label, Marks


def make_raf(
    folder: Path, name: str, *, crop=False, exposure=None, marks=None
) -> Path:
    """An empty RAF, with a sidecar when an edit or mark is wanted."""
    path = folder / name
    path.write_bytes(b"not a real raf")
    data: dict[str, object] = {}
    if crop:
        data["crop"] = {"rect": [0.1, 0.1, 0.8, 0.8]}
    if exposure is not None:
        data["exposure"] = exposure
    if marks is not None:
        data["marks"] = marks
    if data:
        sidecar = path.with_name(path.name + ".grawji.json")
        sidecar.write_text(json.dumps(data))
    return path


def entry(name: str, **fields) -> Entry:
    """An entry without touching the disk."""
    return Entry(path=f"/tmp/{name}", name=name, **fields)


def test_scan_finds_both_spellings_in_name_order(tmp_path):
    """A card reader may leave either spelling behind."""
    make_raf(tmp_path, "b.RAF")
    make_raf(tmp_path, "a.raf")
    (tmp_path / "note.txt").write_text("ignored")
    assert [e.name for e in catalog.scan(tmp_path)] == ["a.raf", "b.RAF"]


def test_scan_reads_the_edit_flags(tmp_path):
    """The badges the strip shows come from the same sidecar."""
    make_raf(tmp_path, "plain.RAF")
    make_raf(tmp_path, "cropped.RAF", crop=True)
    make_raf(tmp_path, "lifted.RAF", exposure=0.33)
    flags = {
        e.name: (e.has_crop, e.has_ev, e.edited)
        for e in catalog.scan(tmp_path)
    }
    assert flags["plain.RAF"] == (False, False, False)
    assert flags["cropped.RAF"] == (True, False, True)
    assert flags["lifted.RAF"] == (False, True, True)


def test_metadata_arrives_after_the_scan():
    """The thumbnail pass fills in what the scan does not read."""
    plain = entry("a.RAF")
    assert plain.model == ""
    filled = catalog.with_meta(plain, "X-E5", "XF23mmF2 R WR", "23.0 mm")
    assert (filled.model, filled.lens, filled.focal_value) == (
        "X-E5",
        "XF23mmF2 R WR",
        23.0,
    )
    assert filled.path == plain.path


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (Order.NAME, ["a.RAF", "b.RAF", "c.RAF"]),
        (Order.SHOT, ["c.RAF", "a.RAF", "b.RAF"]),
        (Order.CAMERA, ["a.RAF", "c.RAF", "b.RAF"]),
        (Order.FOCAL, ["c.RAF", "a.RAF", "b.RAF"]),
    ],
)
def test_every_order_sorts_by_what_it_says(order, expected):
    """Each order puts the folder in its own sequence."""
    entries = [
        entry(
            "a.RAF", model="X-E5", focal="23.0 mm", shot="2026:09:08 19:01:00"
        ),
        entry(
            "b.RAF", model="X-T3", focal="56.0 mm", shot="2026:09:08 19:02:00"
        ),
        entry(
            "c.RAF", model="X-E5", focal="16.0 mm", shot="2026:09:08 18:59:00"
        ),
    ]
    assert [e.name for e in catalog.sort_entries(entries, order)] == expected


def test_an_unknown_focal_sorts_last():
    """A frame without focal data must not break the order."""
    entries = [entry("no.RAF"), entry("yes.RAF", focal="35.0 mm")]
    order = catalog.sort_entries(entries, Order.FOCAL)
    assert [e.name for e in order] == ["yes.RAF", "no.RAF"]


def test_reverse_turns_the_order_around():
    """Newest first is the same order, read backwards."""
    entries = [entry("a.RAF"), entry("b.RAF")]
    assert [e.name for e in catalog.sort_entries(entries, reverse=True)] == [
        "b.RAF",
        "a.RAF",
    ]


def test_an_empty_filter_hides_nothing():
    """Without a rule every frame passes and nothing is filtered."""
    empty = Filter()
    assert not empty.is_active
    assert empty.matches(entry("a.RAF", model="X-E5"))


def test_each_rule_narrows_the_folder():
    """Camera, lens, focal range and the edited flag all filter."""
    shot = entry("a.RAF", model="X-E5", lens="XF23mmF2", focal="23.0 mm")
    assert Filter(camera="X-E5").matches(shot)
    assert not Filter(camera="X-T3").matches(shot)
    assert Filter(lens="XF23mmF2").matches(shot)
    assert not Filter(lens="XF56mmF1.2").matches(shot)
    assert Filter(focal=(16.0, 35.0)).matches(shot)
    assert not Filter(focal=(50.0, 200.0)).matches(shot)
    assert not Filter(edited_only=True).matches(shot)
    assert Filter(edited_only=True).matches(entry("b.RAF", has_crop=True))


def test_a_frame_without_metadata_still_passes():
    """Nothing may vanish while the thumbnail pass is still running."""
    pending = entry("a.RAF")
    assert Filter(camera="X-E5").matches(pending)
    assert Filter(focal=(16.0, 35.0)).matches(pending)
    # The edited flag comes from the scan, so it applies immediately.
    assert not Filter(edited_only=True).matches(pending)


def test_apply_filters_and_orders_in_one_step():
    """What a view asks for: the frames to show, in sequence."""
    entries = [
        entry("a.RAF", model="X-E5", focal="23.0 mm"),
        entry("b.RAF", model="X-T3", focal="56.0 mm"),
        entry("c.RAF", model="X-E5", focal="16.0 mm"),
    ]
    shown = catalog.apply(
        entries, order=Order.FOCAL, entry_filter=Filter(camera="X-E5")
    )
    assert [e.name for e in shown] == ["c.RAF", "a.RAF"]


def test_the_popover_lists_only_what_is_present():
    """The filter offers the folder's own cameras, lenses and stops."""
    entries = [
        entry("a.RAF", model="X-E5", lens="XF23mmF2", focal="23.0 mm"),
        entry("b.RAF", model="X-T3", lens="XF23mmF2", focal="23.0 mm"),
        entry("c.RAF"),
    ]
    assert catalog.cameras(entries) == ["X-E5", "X-T3"]
    assert catalog.lenses(entries) == ["XF23mmF2"]
    assert catalog.focal_labels(entries) == ["23.0 mm"]


def test_an_edit_updates_the_entry():
    """Saving a crop must reach the model, or the filter goes stale."""
    plain = entry("a.RAF")
    edited = catalog.with_edits(plain, True, False)
    assert not plain.edited
    assert edited.edited and edited.has_crop and not edited.has_ev


def test_scan_reads_the_marks(tmp_path):
    """The marks come from the same single sidecar read."""
    make_raf(tmp_path, "plain.RAF")
    make_raf(tmp_path, "rated.RAF", marks={"rating": 4, "labels": ["red"]})
    marks = {e.name: e.marks for e in catalog.scan(tmp_path)}
    assert marks["plain.RAF"] == Marks()
    assert marks["rated.RAF"] == Marks(rating=4, labels=frozenset({Label.RED}))


def test_scan_pairs_the_camera_jpeg(tmp_path):
    """A RAW+JPEG shot shows as one entry that knows its JPEG."""
    make_raf(tmp_path, "pair.RAF")
    make_raf(tmp_path, "single.RAF")
    (tmp_path / "pair.JPG").write_bytes(b"jpg")
    (tmp_path / "orphan.JPG").write_bytes(b"jpg")
    entries = {e.name: e for e in catalog.scan(tmp_path)}
    assert set(entries) == {"pair.RAF", "single.RAF"}
    assert entries["pair.RAF"].jpeg == str(tmp_path / "pair.JPG")
    assert entries["single.RAF"].jpeg is None


def test_the_mark_rules_narrow_the_folder():
    """Stars, labels, the export mark and rejects all filter."""
    rated = entry(
        "a.RAF",
        marks=Marks(rating=3, labels=frozenset({Label.RED}), export=True),
    )
    plain = entry("b.RAF")
    rejected = entry("c.RAF", marks=Marks(rating=-1))
    assert Filter(min_rating=3).is_active
    assert Filter(min_rating=3).matches(rated)
    assert not Filter(min_rating=4).matches(rated)
    assert not Filter(min_rating=1).matches(rejected)
    assert Filter(labels=frozenset({Label.RED, Label.BLUE})).matches(rated)
    assert not Filter(labels=frozenset({Label.BLUE})).matches(rated)
    assert Filter(export_only=True).matches(rated)
    assert not Filter(export_only=True).matches(plain)
    hide = Filter(rejects=Rejects.HIDE)
    only = Filter(rejects=Rejects.ONLY)
    assert hide.matches(plain) and not hide.matches(rejected)
    assert only.matches(rejected) and not only.matches(plain)


def test_the_mark_rules_apply_before_metadata_arrives():
    """Marks come from the scan, so they filter immediately."""
    pending = entry("a.RAF")
    assert not pending.has_meta
    assert not Filter(min_rating=1).matches(pending)


def test_rating_order_puts_rejects_first_and_stars_last():
    """Rated order is ascending like every other order."""
    entries = [
        entry("a.RAF", marks=Marks(rating=5)),
        entry("b.RAF", marks=Marks(rating=-1)),
        entry("c.RAF"),
        entry("d.RAF", marks=Marks(rating=2)),
    ]
    order = catalog.sort_entries(entries, Order.RATING)
    assert [e.name for e in order] == ["b.RAF", "c.RAF", "d.RAF", "a.RAF"]


def test_new_marks_update_the_entry():
    """Marking must reach the model, or the filter goes stale."""
    plain = entry("a.RAF")
    marked = catalog.with_marks(plain, Marks(export=True))
    assert marked.marks.export
    assert not plain.marks.export


def test_the_camera_rating_stands_in_for_none():
    """A never rated image shows the rating the camera wrote."""
    plain = entry("a.RAF")
    seeded = catalog.with_meta(plain, "X-E5", "", "", 3)
    assert seeded.camera_rating == 3
    assert seeded.marks.rating == 3


def test_a_rating_of_the_person_wins_over_the_camera():
    """Set or cleared in grawji, the camera's rating stays out."""
    rated = entry("a.RAF", marks=Marks(rating=5))
    cleared = entry("b.RAF", marks=Marks(rating=0))
    assert catalog.with_meta(rated, "X-E5", "", "", 3).marks.rating == 5
    assert catalog.with_meta(cleared, "X-E5", "", "", 3).marks.rating == 0
    assert catalog.seeded(Marks(), 0) == Marks()
