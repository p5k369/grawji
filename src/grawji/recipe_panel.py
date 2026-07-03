"""The recipe side panel: every image-quality control, in Fuji menu order."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from typing import Any, ClassVar

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import rawji
from gi.repository import Adw, GObject, Gtk
from rawji.fuji_enums import (
    FP_TONE_MAX,
    FP_TONE_MIN,
    WB_KELVIN_PRESETS,
    ChromeEffect,
    ColorSpace,
    GrainEffect,
    GrainEffectSize,
)

from grawji.capabilities import FILM_SIMULATIONS, Capabilities
from grawji.recipe import Recipe
from grawji.widgets import SliderRow, WBShiftGrid

_FILM_SIMULATIONS = list(FILM_SIMULATIONS)
_WHITE_BALANCES = [e.name for e in rawji.WhiteBalance]
_DYNAMIC_RANGES = [e.name for e in rawji.DynamicRange]
_GRAINS = [e.name for e in GrainEffect]
_GRAIN_SIZES = [e.name for e in GrainEffectSize]
_CHROME = [e.name for e in ChromeEffect]
_COLOR_SPACES = [member.name for member in ColorSpace]
_WB_KELVIN_PRESETS = sorted(WB_KELVIN_PRESETS)

# Below this, an exposure value counts as zero EV (avoids "-0.0 EV").
_EV_EPSILON = 1e-9

_UI = (
    resources.files("grawji")
    .joinpath("ui", "recipe_panel.ui")
    .read_text(encoding="utf-8")
)


def _nearest_kelvin_index(kelvin: int) -> int:
    """Return the Kelvin preset index closest to the given value."""
    return min(
        range(len(_WB_KELVIN_PRESETS)),
        key=lambda i: abs(_WB_KELVIN_PRESETS[i] - kelvin),
    )


def _tone_fmt(step: float) -> Callable[[float], str]:
    """Value formatter for a tone slider with the given snap step."""
    if step >= 1:
        return lambda v: f"{round(v):+d}" if round(v) else "0"
    return lambda v: f"{v:+g}" if v else "0"


@Gtk.Template(string=_UI)
class RecipePanel(Adw.PreferencesPage):
    """Edit the current recipe; offers only what the camera supports.

    The panel reads and writes ~grawji.recipe.Recipe values, gates its
    rows on ~grawji.capabilities.Capabilities and tracks which saved
    recipe (or source) the controls came from, showing "(modified)" once
    they diverge. It emits "changed" on any user edit and "apply-recipe"
    with a name chosen from the saved-recipes combo.
    """

    __gtype_name__ = "GrawjiRecipePanel"

    __gsignals__: ClassVar[dict[str, Any]] = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "apply-recipe": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    recipe_row = Gtk.Template.Child()
    recipe_group = Gtk.Template.Child()

    def __init__(self, **kwargs: object) -> None:
        """Build the rows (in Fuji IQ-menu order) and wire their signals."""
        super().__init__(**kwargs)
        # Refined per body on open. Start with everything so the panel
        # looks complete before the first image.
        self._caps: Capabilities | None = None
        self._film_sims: list[str] = list(_FILM_SIMULATIONS)
        self._suppress_signals = False
        self._suppress_combo_signal = False
        self._recipe_names: list[str] = []
        self._applied_recipe = Recipe()
        self._active_label = "Default"

        self._build_rows()
        self._connect_signals()
        self._update_status()

    def _build_rows(self) -> None:
        """Build every recipe control in the Fuji IQ-menu order."""

        def ifmt(value: float) -> str:
            n = round(value)
            return f"{n:+d}" if n else "0"

        def evfmt(value: float) -> str:
            return f"{value:+.1f} EV" if abs(value) > _EV_EPSILON else "0 EV"

        def combo(title: str, names: list[str]) -> Adw.ComboRow:
            row = Adw.ComboRow(title=title)
            row.set_model(Gtk.StringList.new(names))
            return row

        self.film_row = combo("Film simulation", _FILM_SIMULATIONS)
        self.grain_row = combo("Grain", _GRAINS)
        self.grain_size_row = combo("Grain size", _GRAIN_SIZES)
        self.chrome_row = combo("Color chrome", _CHROME)
        self.chrome_blue_row = combo("Color chrome FX blue", _CHROME)
        self.smooth_skin_row = combo("Smooth skin", _CHROME)
        self.wb_row = combo("White balance", _WHITE_BALANCES)
        self.dr_row = combo("Dynamic range", _DYNAMIC_RANGES)
        self.color_space_row = combo("Color space", _COLOR_SPACES)
        self._combo_rows = (
            self.film_row,
            self.grain_row,
            self.grain_size_row,
            self.chrome_row,
            self.chrome_blue_row,
            self.smooth_skin_row,
            self.wb_row,
            self.dr_row,
            self.color_space_row,
        )

        self._temp_row = SliderRow(
            "Color temp",
            lower=0,
            upper=len(_WB_KELVIN_PRESETS) - 1,
            step=1,
            fmt=lambda i: f"{_WB_KELVIN_PRESETS[round(i)]}K",
        )
        self._wb_grid = WBShiftGrid()
        self._wb_shift_label = Gtk.Label(halign=Gtk.Align.CENTER)
        self._wb_shift_label.add_css_class("dim-label")
        wb_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        wb_box.set_valign(Gtk.Align.CENTER)
        wb_box.set_margin_top(8)
        wb_box.set_margin_bottom(8)
        wb_box.append(self._wb_grid)
        wb_box.append(self._wb_shift_label)
        grid_row = Adw.ActionRow(title="WB shift")
        grid_row.add_suffix(wb_box)
        self._update_wb_shift_label()

        self._exposure_row = SliderRow(
            "Exposure", lower=-2.0, upper=3.0, step=1 / 3, fmt=evfmt
        )
        self._highlights_row = SliderRow(
            "Highlights", lower=-2, upper=4, fmt=ifmt
        )
        self._shadows_row = SliderRow("Shadows", lower=-2, upper=4, fmt=ifmt)
        self._color_row = SliderRow(
            "Color", lower=FP_TONE_MIN, upper=FP_TONE_MAX, fmt=ifmt
        )
        self._sharpness_row = SliderRow(
            "Sharpness", lower=FP_TONE_MIN, upper=FP_TONE_MAX, fmt=ifmt
        )
        self._nr_row = SliderRow(
            "Noise reduction", lower=-4, upper=4, fmt=ifmt
        )
        self._clarity_row = SliderRow("Clarity", lower=-5, upper=5, fmt=ifmt)
        self._slider_rows = (
            self._exposure_row,
            self._highlights_row,
            self._shadows_row,
            self._color_row,
            self._sharpness_row,
            self._nr_row,
            self._clarity_row,
            self._temp_row,
        )
        value_chars = max(row.value_chars for row in self._slider_rows)
        for row in self._slider_rows:
            row.set_value_chars(value_chars)

        # Fuji IQ-menu order: film, grain, WB (+ temp + shift), dynamic
        # range, then exposure leading the tonal block.
        for row in (
            self.film_row,
            self.grain_row,
            self.grain_size_row,
            self.chrome_row,
            self.chrome_blue_row,
            self.smooth_skin_row,
            self.wb_row,
            self._temp_row,
            grid_row,
            self.dr_row,
            self._exposure_row,
            self._highlights_row,
            self._shadows_row,
            self._color_row,
            self._sharpness_row,
            self._nr_row,
            self._clarity_row,
            self.color_space_row,
        ):
            self.recipe_group.add(row)
        self._update_temp_visibility()
        self._update_grain_size_visibility()

    def _connect_signals(self) -> None:
        """Route every row's edits into the panel's changed signal."""
        self.recipe_row.connect("notify::selected", self._on_recipe_selected)
        for row in self._combo_rows:
            row.connect("notify::selected", self._on_edited)
        for slider in self._slider_rows:
            slider.connect_changed(self._on_edited)
        self._wb_grid.connect_changed(self._on_wb_shift_changed)
        self.wb_row.connect("notify::selected", self._on_wb_mode_changed)
        self.grain_row.connect("notify::selected", self._on_grain_changed)

    def get_recipe(self) -> Recipe:
        """Read the current selector values into a Recipe."""
        red, blue = self._wb_grid.get_values()
        return Recipe(
            film_simulation=self._film_sims[self.film_row.get_selected()],
            white_balance=_WHITE_BALANCES[self.wb_row.get_selected()],
            dynamic_range=_DYNAMIC_RANGES[self.dr_row.get_selected()],
            grain=_GRAINS[self.grain_row.get_selected()],
            grain_size=_GRAIN_SIZES[self.grain_size_row.get_selected()],
            color_chrome=_CHROME[self.chrome_row.get_selected()],
            color_chrome_blue=_CHROME[self.chrome_blue_row.get_selected()],
            smooth_skin=_CHROME[self.smooth_skin_row.get_selected()],
            exposure=self._exposure_row.get_value(),
            highlights=self._highlights_row.get_value(),
            shadows=self._shadows_row.get_value(),
            color=int(self._color_row.get_value()),
            sharpness=int(self._sharpness_row.get_value()),
            noise_reduction=int(self._nr_row.get_value()),
            clarity=int(self._clarity_row.get_value()),
            wb_shift_r=red,
            wb_shift_b=blue,
            color_temp=_WB_KELVIN_PRESETS[int(self._temp_row.get_value())],
            color_space=_COLOR_SPACES[self.color_space_row.get_selected()],
        )

    def set_recipe(self, recipe: Recipe) -> None:
        """Set the selectors from a recipe without emitting changed."""
        self._suppress_signals = True
        try:
            self.film_row.set_selected(
                self._film_sims.index(recipe.film_simulation)
                if recipe.film_simulation in self._film_sims
                else 0
            )
            self.wb_row.set_selected(
                _WHITE_BALANCES.index(recipe.white_balance)
            )
            self.dr_row.set_selected(
                _DYNAMIC_RANGES.index(recipe.dynamic_range)
            )
            self.grain_row.set_selected(_GRAINS.index(recipe.grain))
            self.grain_size_row.set_selected(
                _GRAIN_SIZES.index(recipe.grain_size)
            )
            self.chrome_row.set_selected(_CHROME.index(recipe.color_chrome))
            self.chrome_blue_row.set_selected(
                _CHROME.index(recipe.color_chrome_blue)
            )
            self.smooth_skin_row.set_selected(
                _CHROME.index(recipe.smooth_skin)
            )
            self._exposure_row.set_value(recipe.exposure)
            self._highlights_row.set_value(recipe.highlights)
            self._shadows_row.set_value(recipe.shadows)
            self._color_row.set_value(recipe.color)
            self._sharpness_row.set_value(recipe.sharpness)
            self._nr_row.set_value(recipe.noise_reduction)
            self._clarity_row.set_value(recipe.clarity)
            self._temp_row.set_value(_nearest_kelvin_index(recipe.color_temp))
            self._wb_grid.set_values(recipe.wb_shift_r, recipe.wb_shift_b)
            self._update_wb_shift_label()
            self.color_space_row.set_selected(
                _COLOR_SPACES.index(recipe.color_space)
            )
        finally:
            self._suppress_signals = False
        self._update_temp_visibility()
        self._update_grain_size_visibility()

    def set_active(self, recipe: Recipe, label: str) -> None:
        """Load a recipe and mark it active (for the recipe indicator)."""
        self._applied_recipe = recipe
        self._active_label = label
        self.set_recipe(recipe)
        self._update_status()
        self.sync_combo(label)

    @property
    def active_label(self) -> str:
        """The label of the recipe/source the controls came from."""
        return self._active_label

    def apply_capabilities(self, caps: Capabilities) -> None:
        """Restrict the recipe controls to what the camera supports.

        The body is identified from the open RAF's EXIF model (a foreign
        RAF fails with 0x2002, so the RAF's body is the connected camera).
        Only when that lookup fails - no model tag, or a model missing
        from the capability table - does the X-Pro2 baseline apply, so
        nothing is offered that the camera might silently ignore.
        """
        self._caps = caps
        self._highlights_row.set_range(caps.tone_min, caps.tone_max)
        self._shadows_row.set_range(caps.tone_min, caps.tone_max)
        # XProcessor5 bodies honour 0.5 tone steps (verified on the X-E5).
        tone_step = 0.5 if caps.tone_half_step else 1.0
        for row in (self._highlights_row, self._shadows_row):
            row.set_step(tone_step, fmt=_tone_fmt(tone_step))
        self.chrome_row.set_visible(caps.has_color_chrome)
        self._clarity_row.set_visible(caps.has_clarity)
        self.chrome_blue_row.set_visible(caps.has_color_chrome_blue)
        self.smooth_skin_row.set_visible(caps.has_smooth_skin)
        self._set_film_simulations(list(caps.film_simulations))
        self._update_grain_size_visibility()

    def set_recipe_names(self, names: list[str]) -> None:
        """Refresh the recipe apply-combo from the saved recipes."""
        self._recipe_names = list(names)
        self._suppress_combo_signal = True
        try:
            self.recipe_row.set_model(
                Gtk.StringList.new(["—", *self._recipe_names])
            )
            self.recipe_row.set_selected(0)
        finally:
            self._suppress_combo_signal = False

    def sync_combo(self, label: str) -> None:
        """Point the apply-combo at label (or "—" if not a recipe)."""
        index = (
            self._recipe_names.index(label) + 1
            if label in self._recipe_names
            else 0
        )
        self._suppress_combo_signal = True
        try:
            self.recipe_row.set_selected(index)
        finally:
            self._suppress_combo_signal = False

    def set_controls_sensitive(self, enabled: bool) -> None:
        """Enable or disable every edit control (not the apply-combo)."""
        for row in (*self._combo_rows, *self._slider_rows, self._wb_grid):
            row.set_sensitive(enabled)
        if enabled:
            self._update_temp_visibility()

    def set_wb_grid_tint(self, colored: bool) -> None:
        """Tint the WB shift grid cells, or not (user preference)."""
        self._wb_grid.set_colored(colored)

    def _set_film_simulations(self, sims: list[str]) -> None:
        """Offer only the given film simulations, keeping the selection."""
        if sims == self._film_sims:
            return
        current = self._film_sims[self.film_row.get_selected()]
        self._film_sims = sims
        self._suppress_signals = True
        try:
            self.film_row.set_model(Gtk.StringList.new(sims))
            self.film_row.set_selected(
                sims.index(current) if current in sims else 0
            )
        finally:
            self._suppress_signals = False

    def _on_recipe_selected(self, *_args: object) -> None:
        """Announce the recipe chosen in the apply-combo."""
        if self._suppress_combo_signal:
            return
        index = self.recipe_row.get_selected()
        if index > 0:
            self.emit("apply-recipe", self._recipe_names[index - 1])

    def _on_edited(self, *_args: object) -> None:
        """Update the indicator and emit changed on a user edit."""
        if self._suppress_signals:
            return
        self._update_status()
        # A manual edit no longer matches a saved recipe.
        self.sync_combo("")
        self.emit("changed")

    def _on_wb_shift_changed(self, _red: int, _blue: int) -> None:
        """Handle a white-balance shift grid edit."""
        self._update_wb_shift_label()
        self._on_edited()

    def _update_wb_shift_label(self) -> None:
        """Show the grid marker's red/blue position next to the grid."""
        red, blue = self._wb_grid.get_values()
        self._wb_shift_label.set_text(f"R {red:+d}  B {blue:+d}")

    def _on_wb_mode_changed(self, *_args: object) -> None:
        """Enable the colour-temp slider only in Temperature mode."""
        self._update_temp_visibility()

    def _update_temp_visibility(self) -> None:
        """Show the colour-temp slider only when WB is Temperature."""
        wb = _WHITE_BALANCES[self.wb_row.get_selected()]
        self._temp_row.set_visible(wb == "Temperature")

    def _on_grain_changed(self, *_args: object) -> None:
        """Hide the grain-size row when there is no grain to size."""
        self._update_grain_size_visibility()

    def _update_grain_size_visibility(self) -> None:
        """Show grain size only when the body has it and grain is on."""
        grain = _GRAINS[self.grain_row.get_selected()]
        supported = self._caps is None or self._caps.has_grain_size
        self.grain_size_row.set_visible(supported and grain != "Off")

    def _update_status(self) -> None:
        """Show the active recipe/source and whether it has been modified."""
        if self.get_recipe() == self._applied_recipe:
            self.recipe_group.set_description(self._active_label)
        else:
            self.recipe_group.set_description(
                f"{self._active_label} (modified)"
            )
