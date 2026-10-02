"""Tests for the marks a person puts on an image."""

from __future__ import annotations

import pytest

from grawji.marks import MAX_RATING, REJECTED, Label, Marks


def test_no_marks_is_empty_and_stores_nothing() -> None:
    """An unmarked image leaves nothing in its sidecar."""
    assert Marks().is_empty
    assert Marks().to_dict() == {}


def test_round_trip_keeps_every_mark() -> None:
    """Rating, labels and export survive the stored form."""
    marks = Marks(
        rating=4, labels=frozenset({Label.BLUE, Label.RED}), export=True
    )
    stored = marks.to_dict()
    assert stored == {"rating": 4, "labels": ["red", "blue"], "export": True}
    assert Marks.from_dict(stored) == marks


def test_a_reject_is_no_stars() -> None:
    """Rejected and starred exclude each other by construction."""
    rejected = Marks(rating=REJECTED)
    assert rejected.rejected
    assert rejected.stars == 0
    assert not Marks(rating=3).rejected
    assert Marks(rating=3).stars == 3


@pytest.mark.parametrize(
    ("given", "kept"),
    [(7, MAX_RATING), (-4, REJECTED), ("3", None), (True, None), (2.5, None)],
)
def test_a_malformed_rating_is_clamped_or_dropped(given, kept) -> None:
    """A hand-edited sidecar cannot push the rating out of range."""
    assert Marks.from_dict({"rating": given}).rating == kept


def test_a_cleared_rating_is_not_no_rating() -> None:
    """Clearing is remembered, so the camera's rating stays out."""
    assert Marks().rating is None
    cleared = Marks(rating=5).with_rating(0)
    assert cleared.rating == 0
    assert not cleared.is_empty
    assert cleared.to_dict() == {"rating": 0}
    assert Marks.from_dict({"rating": 0}) == cleared
    assert cleared.stars == 0


def test_unknown_labels_and_junk_are_ignored() -> None:
    """A label from a newer version must not break an older one."""
    marks = Marks.from_dict({"labels": ["green", "teal", 3], "export": "yes"})
    assert marks.labels == {Label.GREEN}
    assert not marks.export
    assert Marks.from_dict(["not", "a", "dict"]) == Marks()


def test_edits_return_new_marks() -> None:
    """Every change is a new value; the original stays as it was."""
    base = Marks()
    labeled = base.with_label(Label.YELLOW, on=True)
    assert labeled.labels == {Label.YELLOW}
    assert labeled.with_label(Label.YELLOW, on=False).labels == frozenset()
    assert base.with_export(on=True).export
    assert base.with_rating(9).rating == MAX_RATING
    assert base == Marks()


def test_labels_are_listed_in_display_order() -> None:
    """Views draw the dots in one fixed order, whatever was set first."""
    marks = Marks(labels=frozenset({Label.PURPLE, Label.RED, Label.GREEN}))
    assert marks.ordered_labels == [Label.RED, Label.GREEN, Label.PURPLE]
