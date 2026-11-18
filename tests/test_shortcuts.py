"""The shortcut registry agrees with the window and with itself."""

from __future__ import annotations

from importlib import resources
from xml.etree import ElementTree as ET

from grawji import shortcuts


def _template_keys() -> dict[str, str]:
    """The bare keys the window template binds, by action."""
    text = (
        resources.files("grawji")
        .joinpath("ui", "grawji.ui")
        .read_text(encoding="utf-8")
    )
    keys: dict[str, str] = {}
    for shortcut in ET.fromstring(text).iter("object"):
        if shortcut.get("class") != "GtkShortcut":
            continue
        props = {
            p.get("name"): p.text or "" for p in shortcut.iter("property")
        }
        action = props["action"].removeprefix("action(win.").removesuffix(")")
        keys[action] = props["trigger"]
    return keys


def _base(action: str) -> str:
    """An action's name without its parameter."""
    return action.split("(", 1)[0]


def test_the_bare_keys_match_the_window_template() -> None:
    """A key added to the template has to be registered, and back."""
    assert _template_keys() == shortcuts.BARE_KEYS


def test_every_bound_action_is_listed_in_the_dialog() -> None:
    """No accelerator goes unmentioned."""
    listed = {
        _base(entry.action)
        for entries in shortcuts.GROUPS.values()
        for entry in entries
        if entry.action
    }
    bound = {_base(a) for a in (*shortcuts.ACCELS, *shortcuts.BARE_KEYS)}
    assert bound - listed == set()


def test_every_listed_action_has_a_key() -> None:
    """An entry naming an action resolves to its accelerator."""
    for entries in shortcuts.GROUPS.values():
        for entry in entries:
            if entry.action:
                assert entry.accelerator, entry.label


def test_no_key_is_bound_twice() -> None:
    """Two actions cannot share one accelerator."""
    keys = [
        accel.lower()
        for accels in shortcuts.ACCELS.values()
        for accel in accels
    ]
    keys.extend(shortcuts.BARE_KEYS.values())
    assert len(keys) == len(set(keys))
