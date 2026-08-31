"""Previous/next controls for the filmstrip: buttons and arrow keys."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk

if TYPE_CHECKING:
    from grawji.views.filmstrip import FilmStrip

# Holding a nav arrow or arrow key longer than this glides instead of
# stepping.
_NAV_HOLD_MS = 350


class FilmStripNav:
    """Previous/next controls for a filmstrip: buttons and arrow keys.

    Each control steps one card on a tap and glides continuously while
    held. The buttons sensitivity follows whether the strip can scroll
    in that direction.
    """

    def __init__(self, strip: FilmStrip) -> None:
        """Build the nav buttons for strip and track its scroll range."""
        self._strip = strip
        self._key_hold: int | None = None
        self._key_dir = 0
        self.prev_button = self._nav_button(
            "go-previous-symbolic", "One image left (hold to scroll)", -1
        )
        self.prev_button.add_css_class("filmstrip-nav-start")
        self.next_button = self._nav_button(
            "go-next-symbolic", "One image right (hold to scroll)", 1
        )
        self.next_button.add_css_class("filmstrip-nav-end")
        adj = strip.get_hadjustment()
        adj.connect("value-changed", lambda *_a: self.update())
        adj.connect("changed", lambda *_a: self.update())
        self.update()

    def attach_keys(self, window: Gtk.Window) -> None:
        """Bind Left/Right on window to the same tap/hold scrolling."""
        self._window = window
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        keys.connect("key-released", self._on_key_released)
        window.add_controller(keys)

    def update(self) -> None:
        """Enable each arrow only when the strip can scroll that way."""
        adj = self._strip.get_hadjustment()
        value = adj.get_value()
        self.prev_button.set_sensitive(value > adj.get_lower())
        self.next_button.set_sensitive(
            value + adj.get_page_size() < adj.get_upper()
        )

    def _nav_button(self, icon: str, tooltip: str, delta: int) -> Gtk.Button:
        """Create a filmstrip scroll button: tap one card, hold to glide."""
        button = Gtk.Button(icon_name=icon)
        button.add_css_class("flat")
        button.set_tooltip_text(tooltip)
        state: dict[str, int | None] = {"hold": None}

        def begin_glide() -> int:
            state["hold"] = None
            self._strip.start_glide(delta)
            return GLib.SOURCE_REMOVE

        def on_pressed(
            gesture: Gtk.GestureClick, _n: int, _x: float, _y: float
        ) -> None:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            state["hold"] = GLib.timeout_add(_NAV_HOLD_MS, begin_glide)

        def settle(*, step: bool) -> None:
            if state["hold"] is not None:
                GLib.source_remove(state["hold"])
                state["hold"] = None
                if step:
                    self._strip.scroll_step(delta)
            else:
                self._strip.stop_glide()

        gesture = Gtk.GestureClick()
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect("pressed", on_pressed)
        gesture.connect("released", lambda *_a: settle(step=True))
        gesture.connect("cancel", lambda *_a: settle(step=False))
        button.add_controller(gesture)
        return button

    def _on_key_pressed(
        self, _controller: Any, keyval: int, _keycode: int, modifiers: Any
    ) -> bool:
        """Start the tap/hold cycle for a bare Left/Right key press."""
        direction = {Gdk.KEY_Left: -1, Gdk.KEY_Right: 1}.get(keyval, 0)
        modifier_mask = Gtk.accelerator_get_default_mod_mask()
        if direction == 0 or (modifiers & modifier_mask):
            return False
        focus = self._window.get_focus()
        if isinstance(focus, Gtk.Editable | Gtk.Text):
            return False
        if self._key_dir == direction:
            return True
        self._key_dir = direction

        def begin_glide() -> int:
            self._key_hold = None
            self._strip.start_glide(direction)
            return GLib.SOURCE_REMOVE

        self._key_hold = GLib.timeout_add(_NAV_HOLD_MS, begin_glide)
        return True

    def _on_key_released(
        self, _controller: Any, keyval: int, _keycode: int, _modifiers: Any
    ) -> None:
        """End the cycle: a quick tap steps one card, a hold stops gliding."""
        direction = {Gdk.KEY_Left: -1, Gdk.KEY_Right: 1}.get(keyval, 0)
        if direction == 0 or direction != self._key_dir:
            return
        self._key_dir = 0
        if self._key_hold is not None:
            GLib.source_remove(self._key_hold)
            self._key_hold = None
            self._strip.scroll_step(direction)
        else:
            self._strip.stop_glide()
