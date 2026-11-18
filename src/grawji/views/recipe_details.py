"""The recipe manager's view of the selected recipe."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk

from grawji.recipe import Recipe
from grawji.recipe_text import film_sim_label, format_recipe_text
from grawji.recipes import HOTKEYS
from grawji.views.recipe_thumb import RecipeThumb

_UI = (
    resources.files("grawji")
    .joinpath("ui", "recipe_details.ui")
    .read_text(encoding="utf-8")
)

_PICTURE_WIDTH = 288
_PICTURE_MAX_HEIGHT = 240
_PLACEHOLDER_HEIGHT = 96

_ = RecipeThumb


@dataclass(frozen=True)
class Shown:
    """Everything the pane shows of one recipe.

    Attributes:
        name: The recipe's name.
        recipe: The recipe.
        texture: Its picture.
        comment: Its comment.
        hotkey: The number key that applies it.
        key_holders: The other recipes holding a number key.
        is_baseline: Whether it is the compare baseline.
        fit: What the connected body cannot do with it.
        fit_css: The style class of the fit note.
    """

    name: str
    recipe: Recipe
    texture: Any
    comment: str
    hotkey: int | None
    key_holders: dict[int, str]
    is_baseline: bool
    fit: str
    fit_css: str


@dataclass
class DetailActions:
    """What the pane's buttons do, each for the shown recipe."""

    apply: Callable[[str], None]
    rename: Callable[[str], None]
    comment: Callable[[str], None]
    picture: Callable[[str], None]
    clear_picture: Callable[[str], None]
    copy_text: Callable[[str], None]
    baseline: Callable[[str, bool], None]
    hotkey: Callable[[str, int | None], None]


@Gtk.Template(string=_UI)
class RecipeDetails(Gtk.Stack):
    """One recipe at a glance: its picture, its values and its actions."""

    __gtype_name__ = "GrawjiRecipeDetails"

    hint_title = Gtk.Template.Child()
    hint_text = Gtk.Template.Child()
    picture = Gtk.Template.Child()
    name_label = Gtk.Template.Child()
    rename_button = Gtk.Template.Child()
    origin_label = Gtk.Template.Child()
    fit_label = Gtk.Template.Child()
    comment_button = Gtk.Template.Child()
    comment_label = Gtk.Template.Child()
    apply_button = Gtk.Template.Child()
    baseline_button = Gtk.Template.Child()
    image_button = Gtk.Template.Child()
    clear_image_button = Gtk.Template.Child()
    copy_button = Gtk.Template.Child()
    values_grid = Gtk.Template.Child()
    key_dropdown = Gtk.Template.Child()

    def __init__(self, **kwargs: Any) -> None:
        """Build the empty pane."""
        super().__init__(**kwargs)
        self._actions: DetailActions | None = None
        self._shown: Shown | None = None
        self._filling = False
        self.key_dropdown.connect("notify::selected", self._on_key_picked)
        for button, handler in (
            (self.apply_button, lambda a, n: a.apply(n)),
            (self.rename_button, lambda a, n: a.rename(n)),
            (self.comment_button, lambda a, n: a.comment(n)),
            (self.image_button, lambda a, n: a.picture(n)),
            (self.clear_image_button, lambda a, n: a.clear_picture(n)),
            (self.copy_button, lambda a, n: a.copy_text(n)),
        ):
            button.connect("clicked", self._on_clicked, handler)
        self.baseline_button.connect("toggled", self._on_baseline_toggled)
        self.show_none()

    def set_actions(self, actions: DetailActions) -> None:
        """Connect the buttons."""
        self._actions = actions

    def show_none(self, count: int = 0) -> None:
        """Show the hint for no selection, or for several recipes."""
        self._shown = None
        if count > 1:
            self.hint_title.set_label(f"{count} recipes selected")
            self.hint_text.set_label(
                "Move, export or delete them with the buttons above the list."
            )
        else:
            self.hint_title.set_label("No recipe selected")
            self.hint_text.set_label(
                "Click a recipe to see it. A double click applies it to"
                " the open image."
            )
        self.set_visible_child_name("none")

    def show_recipe(self, shown: Shown) -> None:
        """Show one recipe."""
        self._shown = shown
        self._filling = True
        recipe = shown.recipe
        self._show_picture(shown.texture)
        self.name_label.set_label(shown.name)
        origin = [film_sim_label(recipe.film_simulation)]
        if recipe.origin_body:
            origin.append(f"from {recipe.origin_body}")
        self.origin_label.set_label(" · ".join(origin))
        self.fit_label.set_label(shown.fit)
        self.fit_label.set_visible(bool(shown.fit))
        for css in ("warning", "error"):
            self.fit_label.remove_css_class(css)
        if shown.fit_css:
            self.fit_label.add_css_class(shown.fit_css)
        self.comment_label.set_label(shown.comment or "Add a comment…")
        if shown.comment:
            self.comment_label.remove_css_class("dim-label")
        else:
            self.comment_label.add_css_class("dim-label")
        self.image_button.set_tooltip_text(
            "Render this recipe on the open image as its picture"
        )
        self.clear_image_button.set_visible(shown.texture is not None)
        self.baseline_button.set_active(shown.is_baseline)
        self._fill_values(recipe)
        self._fill_keys(shown)
        self._filling = False
        self.set_visible_child_name("one")

    def _fill_values(self, recipe: Recipe) -> None:
        """List the recipe's settings as label and value."""
        while (child := self.values_grid.get_first_child()) is not None:
            self.values_grid.remove(child)
        lines = format_recipe_text(recipe).splitlines()
        for row, line in enumerate(lines):
            label, _, value = line.partition(": ")
            left = Gtk.Label(label=label, xalign=0)
            left.add_css_class("dim-label")
            right = Gtk.Label(label=value, xalign=0, hexpand=True)
            self.values_grid.attach(left, 0, row, 1, 1)
            self.values_grid.attach(right, 1, row, 1, 1)

    def _fill_keys(self, shown: Shown) -> None:
        """Offer the number keys, naming the recipes that hold one."""
        labels = ["None"]
        for key in HOTKEYS:
            holder = shown.key_holders.get(key)
            if holder and holder != shown.name:
                labels.append(f"{key} · now {holder}")
            else:
                labels.append(str(key))
        self.key_dropdown.set_model(Gtk.StringList.new(labels))
        index = HOTKEYS.index(shown.hotkey) + 1 if shown.hotkey else 0
        self.key_dropdown.set_selected(index)

    def _show_picture(self, texture: Any) -> None:
        """Size the tile to the picture."""
        if texture is None:
            width, height = _PICTURE_WIDTH, _PLACEHOLDER_HEIGHT
        else:
            ratio = texture.get_height() / texture.get_width()
            height = min(round(_PICTURE_WIDTH * ratio), _PICTURE_MAX_HEIGHT)
            width = min(_PICTURE_WIDTH, round(height / ratio))
        self.picture.set_tile_size(width, height)
        self.picture.set_texture(texture)

    def _on_clicked(
        self, _button: Any, handler: Callable[[DetailActions, str], None]
    ) -> None:
        """Run a button's action for the shown recipe."""
        if self._actions is not None and self._shown is not None:
            handler(self._actions, self._shown.name)

    def _on_baseline_toggled(self, button: Gtk.ToggleButton) -> None:
        """Set or clear the compare baseline."""
        if self._filling or self._actions is None or self._shown is None:
            return
        self._actions.baseline(self._shown.name, button.get_active())

    def _on_key_picked(self, dropdown: Gtk.DropDown, _param: Any) -> None:
        """Assign the picked number key."""
        if self._filling or self._actions is None or self._shown is None:
            return
        index = dropdown.get_selected()
        key = HOTKEYS[index - 1] if 0 < index <= len(HOTKEYS) else None
        if key != self._shown.hotkey:
            self._actions.hotkey(self._shown.name, key)
