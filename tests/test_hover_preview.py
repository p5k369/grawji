"""Tests for the film simulation hover preview logic."""

from dataclasses import replace

from grawji.hover_preview import (
    HoverActions,
    HoverPreview,
    RenderCache,
    recipe_key,
)
from grawji.recipe import Recipe


class FakeHost:
    """Records what the controller asks of the window; timers run by hand."""

    def __init__(self):
        """Start with no timers, renders or display changes."""
        self.timers = {}
        self.next_handle = 1
        self.renders = []
        self.shown = []
        self.restored = []
        self.current_renders = 0
        self.screen = "panel picture"

    def actions(self):
        """The HoverActions bound to this host."""
        return HoverActions(
            schedule=self.schedule,
            cancel=self.timers.pop,
            render=self.renders.append,
            show_hover=lambda jpeg, r: self.shown.append(("hover", jpeg)),
            show_current=lambda jpeg, r: self.shown.append(("current", jpeg)),
            snapshot=lambda: self.screen,
            restore=self.restored.append,
            render_current=self.render_current,
        )

    def schedule(self, delay_ms, callback):
        """Remember a timer; return its handle."""
        handle = self.next_handle
        self.next_handle += 1
        self.timers[handle] = callback
        return handle

    def fire_timers(self):
        """Run every pending timer (the pointer settled)."""
        timers, self.timers = self.timers, {}
        for callback in timers.values():
            callback()

    def render_current(self):
        """Count normal renders of the panel's recipe."""
        self.current_renders += 1


def make(cache=None):
    """A controller wired to a fresh fake host."""
    host = FakeHost()
    cache = cache if cache is not None else RenderCache()
    return HoverPreview(host.actions(), cache), host, cache


CURRENT = Recipe(film_simulation="Provia", grain="Weak")


def with_sim(name):
    """The current recipe with another film simulation."""
    return replace(CURRENT, film_simulation=name)


def test_hover_renders_after_settling():
    """An uncached hover waits for the pointer to settle, then renders."""
    hover, host, _ = make()
    hover.hover(with_sim("Velvia"), CURRENT)
    assert host.renders == []
    host.fire_timers()
    assert host.renders == [with_sim("Velvia")]
    hover.rendered(with_sim("Velvia"), b"velvia")
    assert host.shown == [("hover", b"velvia")]


def test_sweeping_the_list_renders_only_where_it_stops():
    """Passing over rows cancels their pending renders."""
    hover, host, _ = make()
    for name in ("Velvia", "Astia", "ClassicChrome"):
        hover.hover(with_sim(name), CURRENT)
    host.fire_timers()
    assert host.renders == [with_sim("ClassicChrome")]


def test_cached_hover_shows_at_once():
    """A simulation rendered before for this image needs no camera."""
    hover, host, cache = make()
    cache.put(with_sim("Velvia"), b"velvia")
    hover.hover(with_sim("Velvia"), CURRENT)
    assert host.shown == [("hover", b"velvia")]
    assert host.timers == {}
    assert host.renders == []


def test_hovering_the_selected_simulation_costs_nothing():
    """The row under the pointer when the list opens is the current one."""
    hover, host, _ = make()
    hover.hover(CURRENT, CURRENT)
    host.fire_timers()
    assert host.renders == []
    assert not hover.active


def test_late_render_after_moving_on_is_cached_not_shown():
    """A render for a row the pointer left is kept but not displayed."""
    hover, host, cache = make()
    hover.hover(with_sim("Velvia"), CURRENT)
    host.fire_timers()
    hover.hover(with_sim("Astia"), CURRENT)
    hover.rendered(with_sim("Velvia"), b"velvia")
    assert host.shown == []
    assert cache.get(with_sim("Velvia")) == b"velvia"
    assert not hover.wants(with_sim("Velvia"))
    assert hover.wants(with_sim("Astia"))


def test_closing_without_a_pick_restores_the_snapshot():
    """Nothing picked and nothing cached: the old picture comes back."""
    hover, host, _ = make()
    hover.hover(with_sim("Velvia"), CURRENT)
    host.fire_timers()
    hover.rendered(with_sim("Velvia"), b"velvia")
    hover.end(CURRENT)
    assert host.restored == ["panel picture"]
    assert host.current_renders == 0
    assert not hover.active


def test_closing_prefers_the_cached_current_render():
    """A current-recipe render that landed mid-hover beats the snapshot."""
    hover, host, cache = make()
    hover.hover(with_sim("Velvia"), CURRENT)
    host.fire_timers()
    hover.rendered(with_sim("Velvia"), b"velvia")
    cache.put(CURRENT, b"fresh current")
    hover.end(CURRENT)
    assert host.shown[-1] == ("current", b"fresh current")
    assert host.restored == []


def test_picking_the_hovered_simulation_shows_it_at_once():
    """The pick reuses the hover render instead of the camera."""
    hover, host, _ = make()
    hover.hover(with_sim("Velvia"), CURRENT)
    host.fire_timers()
    hover.rendered(with_sim("Velvia"), b"velvia")
    hover.end(with_sim("Velvia"))
    assert host.shown[-1] == ("current", b"velvia")
    assert host.current_renders == 0


def test_picking_an_unrendered_simulation_renders_normally():
    """A pick that was never rendered falls back to a normal render."""
    hover, host, _ = make()
    hover.hover(with_sim("Velvia"), CURRENT)
    host.fire_timers()
    hover.rendered(with_sim("Velvia"), b"velvia")
    hover.end(with_sim("Eterna"))
    assert host.current_renders == 1
    assert host.restored == []


def test_closing_before_anything_showed_changes_nothing():
    """No hover picture on screen means nothing to put back."""
    hover, host, _ = make()
    hover.hover(with_sim("Velvia"), CURRENT)
    hover.end(CURRENT)
    assert host.timers == {}
    assert host.restored == []
    assert host.shown == []
    assert host.current_renders == 0


def test_back_on_the_selected_row_puts_the_panel_picture_back():
    """Returning to the current row mid-hover undoes the preview."""
    hover, host, _ = make()
    hover.hover(with_sim("Velvia"), CURRENT)
    host.fire_timers()
    hover.rendered(with_sim("Velvia"), b"velvia")
    hover.hover(CURRENT, CURRENT)
    assert host.restored == ["panel picture"]
    assert not hover.active


def test_reset_forgets_cache_and_pending_work():
    """Opening another image drops state, timers and cached previews."""
    hover, host, cache = make()
    cache.put(with_sim("Astia"), b"astia")
    hover.hover(with_sim("Velvia"), CURRENT)
    hover.reset()
    host.fire_timers()
    assert host.renders == []
    assert len(cache) == 0
    assert not hover.active


def test_cache_evicts_least_recently_used():
    """The cache stays bounded and keeps what was used recently."""
    cache = RenderCache(capacity=2)
    cache.put(with_sim("Velvia"), b"v")
    cache.put(with_sim("Astia"), b"a")
    assert cache.get(with_sim("Velvia")) == b"v"
    cache.put(with_sim("Eterna"), b"e")
    assert cache.get(with_sim("Astia")) is None
    assert cache.get(with_sim("Velvia")) == b"v"
    assert len(cache) == 2


def test_origin_body_does_not_split_the_cache():
    """The authoring body never changes a render."""
    assert recipe_key(CURRENT) == recipe_key(
        replace(CURRENT, origin_body="X-T3")
    )
    assert recipe_key(CURRENT) != recipe_key(with_sim("Velvia"))
