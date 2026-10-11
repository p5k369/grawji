"""Tests for the pure undo and redo history."""

from __future__ import annotations

from dataclasses import replace

from grawji.edit_history import EditHistory, Snapshot, changed_fields
from grawji.recipe import Recipe


class Clock:
    """A clock the tests advance by hand."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """The current time."""
        return self.now


def _state(recipe: Recipe, label: str = "Base") -> Snapshot:
    """A snapshot whose controls match the applied recipe."""
    return Snapshot(recipe=recipe, applied=recipe, label=label)


def _edit(base: Snapshot, **fields: object) -> Snapshot:
    """The base snapshot with some controls moved."""
    return replace(base, recipe=replace(base.recipe, **fields))


def _history(
    clock: Clock | None = None,
    *,
    limit: int = 100,
    merge_window: float = 1.0,
) -> EditHistory:
    """A history on a hand-driven clock, reset to a base state."""
    history = EditHistory(
        limit=limit, merge_window=merge_window, clock=clock or Clock()
    )
    history.reset(_state(Recipe()))
    return history


def test_changed_fields_names_every_moved_field() -> None:
    """The diff names exactly the fields whose values differ."""
    before = Recipe()
    after = replace(before, color=2, film_simulation="Velvia")
    assert changed_fields(before, after) == {"color", "film_simulation"}
    assert changed_fields(before, before) == set()


def test_empty_history_offers_nothing() -> None:
    """Before any state there is nothing to undo or redo."""
    history = EditHistory()
    assert history.head is None
    assert not history.can_undo
    assert not history.can_redo
    assert history.undo() is None
    assert history.redo() is None


def test_undo_steps_back_and_redo_forward() -> None:
    """Each discrete push is one step back, and forward again."""
    history = _history()
    base = history.head
    assert base is not None
    first = _edit(base, color=1)
    second = _edit(first, sharpness=2)
    history.push(first)
    history.push(second)
    assert history.can_undo
    assert not history.can_redo
    assert history.undo() == first
    assert history.undo() == base
    assert history.undo() is None
    assert history.redo() == first
    assert history.redo() == second
    assert history.redo() is None


def test_push_after_undo_drops_the_redo_tail() -> None:
    """A new edit after undoing forgets the undone states."""
    history = _history()
    base = history.head
    assert base is not None
    history.push(_edit(base, color=1))
    history.undo()
    other = _edit(base, sharpness=1)
    history.push(other)
    assert not history.can_redo
    assert history.head == other
    assert history.undo() == base


def test_the_same_state_again_is_not_an_entry() -> None:
    """Pushing the head again leaves the history alone."""
    history = _history()
    base = history.head
    assert base is not None
    history.push(_edit(base, color=1))
    history.push(_edit(base, color=1))
    history.undo()
    assert not history.can_undo


def test_a_drag_merges_into_one_entry() -> None:
    """Quick moves of one slider undo in a single step."""
    clock = Clock()
    history = _history(clock)
    base = history.head
    assert base is not None
    for value in (1, 2, 3):
        clock.now += 0.1
        history.push(_edit(base, color=value), merge=True)
    assert history.head == _edit(base, color=3)
    assert history.undo() == base
    assert not history.can_undo


def test_a_pause_starts_a_new_entry() -> None:
    """Returning to a slider after the merge window is its own step."""
    clock = Clock()
    history = _history(clock, merge_window=1.0)
    base = history.head
    assert base is not None
    history.push(_edit(base, color=1), merge=True)
    clock.now += 5.0
    history.push(_edit(base, color=2), merge=True)
    assert history.undo() == _edit(base, color=1)
    assert history.undo() == base


def test_another_control_breaks_the_merge() -> None:
    """Moving a second slider never folds into the first slider's entry."""
    clock = Clock()
    history = _history(clock)
    base = history.head
    assert base is not None
    history.push(_edit(base, color=1), merge=True)
    clock.now += 0.1
    history.push(_edit(base, color=1, sharpness=1), merge=True)
    clock.now += 0.1
    history.push(_edit(base, color=2, sharpness=1), merge=True)
    assert history.undo() == _edit(base, color=1, sharpness=1)
    assert history.undo() == _edit(base, color=1)
    assert history.undo() == base


def test_a_discrete_push_never_merges() -> None:
    """Picking from a combo twice in a row keeps both steps."""
    clock = Clock()
    history = _history(clock)
    base = history.head
    assert base is not None
    history.push(_edit(base, film_simulation="Astia"))
    clock.now += 0.1
    history.push(_edit(base, film_simulation="Velvia"))
    assert history.undo() == _edit(base, film_simulation="Astia")


def test_a_grid_drag_moving_both_axes_merges() -> None:
    """The white balance grid is one control even when both shifts move."""
    clock = Clock()
    history = _history(clock)
    base = history.head
    assert base is not None
    history.push(_edit(base, wb_shift_r=1), merge=True)
    clock.now += 0.1
    history.push(_edit(base, wb_shift_r=2, wb_shift_b=-1), merge=True)
    clock.now += 0.1
    history.push(_edit(base, wb_shift_r=2, wb_shift_b=-2), merge=True)
    assert history.undo() == base


def test_applying_a_recipe_is_its_own_step() -> None:
    """A load changes what the controls are measured against, never merged."""
    clock = Clock()
    history = _history(clock)
    base = history.head
    assert base is not None
    edited = _edit(base, color=1)
    history.push(edited, merge=True)
    clock.now += 0.1
    loaded = _state(Recipe(film_simulation="Velvia", color=1), "Summer")
    history.push(loaded, merge=True)
    assert history.undo() == edited
    assert history.undo() == base


def test_a_relabel_replaces_the_head() -> None:
    """Renaming the active recipe is not an undoable edit."""
    history = _history()
    base = history.head
    assert base is not None
    history.push(_state(Recipe(color=1), "Old"))
    history.push(_state(Recipe(color=1), "New"))
    assert history.head == _state(Recipe(color=1), "New")
    assert history.undo() == base


def test_amend_rewrites_the_head_in_place() -> None:
    """An exposure set after a load belongs to that load's state."""
    history = _history()
    base = history.head
    assert base is not None
    loaded = _state(Recipe(film_simulation="Velvia"), "Summer")
    history.push(loaded)
    with_ev = _edit(loaded, exposure=0.67)
    history.amend(with_ev)
    assert history.head == with_ev
    assert history.undo() == base
    assert history.redo() == with_ev


def test_amend_on_an_empty_history_starts_it() -> None:
    """Amending before any state behaves like a reset."""
    history = EditHistory()
    history.amend(_state(Recipe()))
    assert history.head == _state(Recipe())
    assert not history.can_undo


def test_reset_forgets_everything() -> None:
    """Opening another image starts a fresh history."""
    history = _history()
    base = history.head
    assert base is not None
    history.push(_edit(base, color=1))
    fresh = _state(Recipe(film_simulation="Acros"), "From image")
    history.reset(fresh)
    assert history.head == fresh
    assert not history.can_undo
    assert not history.can_redo


def test_the_limit_drops_the_oldest_state() -> None:
    """The history keeps at most its limit, forgetting from the start."""
    history = _history(limit=3)
    base = history.head
    assert base is not None
    for value in (1, 2, 3):
        history.push(_edit(base, color=value))
    assert history.undo() == _edit(base, color=2)
    assert history.undo() == _edit(base, color=1)
    assert history.undo() is None


def test_undo_then_a_drag_does_not_merge_into_the_past() -> None:
    """After an undo, the next slider move is a fresh entry."""
    clock = Clock()
    history = _history(clock)
    base = history.head
    assert base is not None
    history.push(_edit(base, color=1), merge=True)
    clock.now += 0.1
    history.push(_edit(base, color=2), merge=True)
    history.undo()
    clock.now += 0.1
    history.push(_edit(base, color=3), merge=True)
    assert history.undo() == base
