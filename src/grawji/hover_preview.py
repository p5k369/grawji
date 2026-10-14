"""Film simulation hover previews: what the preview shows while picking."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from grawji.recipe import Recipe

RecipeKey = tuple[tuple[str, object], ...]

# Settle time before a hovered film simulation is sent to the camera, so
# sweeping the pointer down the list does not queue a render per row.
HOVER_DELAY_MS = 120

# One entry per film simulation of the richest body, plus room for edits.
CACHE_SIZE = 32


def recipe_key(recipe: Recipe) -> RecipeKey:
    """Hashable identity of everything in a recipe that affects a render.

    origin_body only badges saved recipes, so two recipes that differ
    there alone render identically and share a key.
    """
    fields = asdict(recipe)
    fields.pop("origin_body", None)
    return tuple(sorted(fields.items()))


class RenderCache:
    """Preview JPEGs of the open image, keyed by recipe (least recent out).

    A camera render is a pure function of the RAF and the recipe, so a
    recipe seen before for the same image never needs the camera again.
    Clear it whenever another image is opened.
    """

    def __init__(self, capacity: int = CACHE_SIZE) -> None:
        """Create an empty cache holding at most capacity previews."""
        self._capacity = capacity
        self._entries: OrderedDict[RecipeKey, bytes] = OrderedDict()

    def get(self, recipe: Recipe) -> bytes | None:
        """Return the cached preview of recipe, or None."""
        key = recipe_key(recipe)
        jpeg = self._entries.get(key)
        if jpeg is not None:
            self._entries.move_to_end(key)
        return jpeg

    def put(self, recipe: Recipe, jpeg: bytes) -> None:
        """Remember the preview rendered for recipe."""
        key = recipe_key(recipe)
        self._entries[key] = jpeg
        self._entries.move_to_end(key)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        """Forget every preview (another image was opened)."""
        self._entries.clear()

    def __len__(self) -> int:
        """Number of cached previews."""
        return len(self._entries)


@dataclass(frozen=True)
class HoverActions:
    """What HoverPreview asks of its host (the window).

    Attributes:
        schedule: Run a callback after a delay in ms; returns a handle.
        cancel: Cancel a handle returned by schedule.
        render: Queue a camera preview render of a hovered recipe. The
            host feeds the result back through HoverPreview.rendered.
        show_hover: Display a hovered recipe's preview. Decoding may be
            asynchronous; check HoverPreview.wants before displaying.
        show_current: Display a preview of the panel's own recipe again.
        snapshot: The picture on screen now (opaque), or None.
        restore: Put a snapshot back on screen.
        render_current: Render the panel's own recipe the normal way.
    """

    schedule: Callable[[int, Callable[[], None]], Any]
    cancel: Callable[[Any], None]
    render: Callable[[Recipe], None]
    show_hover: Callable[[bytes, Recipe], None]
    show_current: Callable[[bytes, Recipe], None]
    snapshot: Callable[[], Any]
    restore: Callable[[Any], None]
    render_current: Callable[[], None]


class HoverPreview:
    """Preview film simulations while the pointer rests on them.

    While the film simulation list is open, hovering a simulation shows
    the open image rendered with it (cached renders at once, others after
    a short settle delay). Closing the list puts the panel's own result
    back: the cached render of the now-current recipe if there is one, so
    picking a hovered simulation shows it instantly, else the picture that
    was on screen before hovering, else a fresh render.
    """

    def __init__(
        self,
        actions: HoverActions,
        cache: RenderCache,
        *,
        delay_ms: int = HOVER_DELAY_MS,
    ) -> None:
        """Create an idle controller.

        Args:
            actions: Host callbacks.
            cache: The open image's preview cache, shared with the host's
                normal render path.
            delay_ms: Settle time before an uncached hover renders.
        """
        self._actions = actions
        self._cache = cache
        self._delay_ms = delay_ms
        self._target: RecipeKey | None = None
        self._pending: Any = None
        self._shown = False
        self._start: RecipeKey | None = None
        self._snapshot: Any = None

    @property
    def active(self) -> bool:
        """Whether a hovered preview is on screen or on its way."""
        return self._target is not None or self._shown

    def wants(self, recipe: Recipe) -> bool:
        """Whether a decoded preview of recipe should still be shown."""
        return self._target is not None and self._target == recipe_key(recipe)

    def hover(self, recipe: Recipe, current: Recipe) -> None:
        """The pointer rests on a film simulation in the open list.

        Args:
            recipe: The panel's recipe with the hovered simulation.
            current: The panel's recipe as it stands.
        """
        key = recipe_key(recipe)
        if key == self._target:
            return
        self._cancel_pending()
        if key == recipe_key(current):
            # Back on the selected simulation (or the list just opened
            # under the pointer): that is the picture already owned by
            # the panel, never worth a camera round trip.
            self._target = None
            if self._shown:
                self._shown = False
                self._put_back(current)
            return
        if self._start is None:
            self._start = recipe_key(current)
            self._snapshot = self._actions.snapshot()
        self._target = key
        cached = self._cache.get(recipe)
        if cached is not None:
            self._shown = True
            self._actions.show_hover(cached, recipe)
            return
        self._pending = self._actions.schedule(
            self._delay_ms, lambda: self._fire(recipe, key)
        )

    def rendered(self, recipe: Recipe, jpeg: bytes) -> None:
        """A hover render finished: cache it, show it if still wanted."""
        self._cache.put(recipe, jpeg)
        if self.wants(recipe):
            self._shown = True
            self._actions.show_hover(jpeg, recipe)

    def end(self, current: Recipe) -> None:
        """The list closed; put the panel's own preview back.

        Args:
            current: The panel's recipe now (a pick has already changed
                it, or will change it right after - either order works,
                because the normal render path reads the same cache).
        """
        self._cancel_pending()
        self._target = None
        if self._shown:
            self._shown = False
            self._put_back(current)
        self._start = None
        self._snapshot = None

    def reset(self) -> None:
        """Forget all hover state and cached previews (image changed)."""
        self._cancel_pending()
        self._target = None
        self._shown = False
        self._start = None
        self._snapshot = None
        self._cache.clear()

    def _fire(self, recipe: Recipe, key: RecipeKey) -> None:
        """Render a hover that settled, unless it was superseded."""
        self._pending = None
        if self._target != key:
            return
        cached = self._cache.get(recipe)
        if cached is not None:
            self._shown = True
            self._actions.show_hover(cached, recipe)
            return
        self._actions.render(recipe)

    def _put_back(self, current: Recipe) -> None:
        """Show the panel's own result after a hover preview."""
        cached = self._cache.get(current)
        if cached is not None:
            self._actions.show_current(cached, current)
        elif recipe_key(current) == self._start and self._snapshot is not None:
            self._actions.restore(self._snapshot)
        else:
            self._actions.render_current()

    def _cancel_pending(self) -> None:
        """Drop a hover render still waiting for the pointer to settle."""
        if self._pending is not None:
            self._actions.cancel(self._pending)
            self._pending = None
