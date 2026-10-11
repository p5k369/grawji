"""Undo and redo over the recipe panel's states."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from grawji.recipe import Recipe

# How many states the history keeps before forgetting the oldest.
DEFAULT_LIMIT = 100
# Slider moves of one control this close together form one entry, so a
# drag undoes in one step while a return to the same control after a
# pause gets its own entry.
MERGE_WINDOW = 1.0
# The grids move two fields in one drag.
_PAIRED_FIELDS = (
    frozenset({"wb_shift_r", "wb_shift_b"}),
    frozenset({"mono_warm_cool", "mono_magenta_green"}),
)


@dataclass(frozen=True)
class Snapshot:
    """Everything the recipe panel shows for one state.

    Attributes:
        recipe: What the controls show, the image's EV included.
        applied: The recipe the controls are measured against for the
            modified marks.
        label: The name of the recipe or source the controls came from.
        unsaved: Whether that recipe is not in the library yet.
    """

    recipe: Recipe
    applied: Recipe
    label: str
    unsaved: bool = False


def changed_fields(before: Recipe, after: Recipe) -> set[str]:
    """The recipe fields whose values differ between two recipes."""
    old, new = before.to_dict(), after.to_dict()
    return {name for name in old if old[name] != new[name]}


class EditHistory:
    """A linear undo stack of panel snapshots with a redo tail."""

    def __init__(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        merge_window: float = MERGE_WINDOW,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start empty.

        Args:
            limit: The most states kept, oldest dropped first.
            merge_window: Seconds within which repeated moves of the same
                control merge into one entry.
            clock: The time source.
        """
        self._limit = max(2, limit)
        self._merge_window = merge_window
        self._clock = clock
        self._states: list[Snapshot] = []
        self._index = -1
        self._merge_field: frozenset[str] | None = None
        self._merge_at = 0.0

    @property
    def head(self) -> Snapshot | None:
        """The current state."""
        return self._states[self._index] if self._states else None

    @property
    def can_undo(self) -> bool:
        """Whether a state lies before the current one."""
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        """Whether an undone state lies after the current one."""
        return 0 <= self._index < len(self._states) - 1

    def reset(self, state: Snapshot) -> None:
        """Forget everything and start over from one state."""
        self._states = [state]
        self._index = 0
        self._merge_field = None

    def push(self, state: Snapshot, *, merge: bool = False) -> None:
        """Record a new current state, dropping any redo tail."""
        head = self.head
        if head is None:
            self.reset(state)
            return
        if state == head:
            return
        del self._states[self._index + 1 :]
        if state.recipe == head.recipe and state.applied == head.applied:
            self._states[self._index] = state
            return
        field = self._merged_field(head, state) if merge else None
        now = self._clock()
        if (
            field is not None
            and field == self._merge_field
            and now - self._merge_at <= self._merge_window
        ):
            self._states[self._index] = state
        else:
            self._states.append(state)
            self._index += 1
            if len(self._states) > self._limit:
                del self._states[0]
                self._index -= 1
        self._merge_field = field
        self._merge_at = now

    def amend(self, state: Snapshot) -> None:
        """Replace the current state without adding an entry."""
        if self.head is None:
            self.reset(state)
        else:
            self._states[self._index] = state

    def undo(self) -> Snapshot | None:
        """Step back one state and return it."""
        if not self.can_undo:
            return None
        self._index -= 1
        self._merge_field = None
        return self._states[self._index]

    def redo(self) -> Snapshot | None:
        """Step forward one undone state and return it."""
        if not self.can_redo:
            return None
        self._index += 1
        self._merge_field = None
        return self._states[self._index]

    @staticmethod
    def _merged_field(
        head: Snapshot, state: Snapshot
    ) -> frozenset[str] | None:
        """The control a state moves from the head, as its fields."""
        if (
            state.applied != head.applied
            or state.label != head.label
            or state.unsaved != head.unsaved
        ):
            return None
        fields = frozenset(changed_fields(head.recipe, state.recipe))
        for pair in _PAIRED_FIELDS:
            if fields <= pair:
                return pair
        return fields if len(fields) == 1 else None
