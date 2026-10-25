"""The recipe manager's camera pane."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk

from grawji.camera import compatibility as compat
from grawji.camera import fs_recipe
from grawji.camera.capabilities import capabilities_for_model
from grawji.recipes import RecipeLibrary
from grawji.views.textures import texture_for_pixbuf

_UI = (
    resources.files("grawji")
    .joinpath("ui", "camera_pane.ui")
    .read_text(encoding="utf-8")
)

# Camera-family hero
_HERO_PX = 44
_HERO_ART_RATIO = 88 / 128
_HERO_SUPERSAMPLE = 4


def _family(model: str | None) -> str | None:
    """Map a model string to a packaged camera-family icon stem."""
    if not model:
        return None
    key = "".join(c for c in model.upper() if c.isalnum())
    if key.startswith("X100"):
        return "x100"
    if key.startswith(("XE", "XPRO", "XM")):
        return "x-e"
    if key.startswith(("XT", "XH", "XS", "GFX")):
        return "x-t"
    return None


def _family_paintable(model: str | None) -> Gdk.Texture | None:
    """A camera-family texture for the model, or None."""
    family = _family(model)
    if family is None:
        return None
    try:
        svg = (
            resources.files("grawji")
            .joinpath("ui", "icons", f"{family}.svg")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError):
        return None
    width = _HERO_PX * _HERO_SUPERSAMPLE
    height = round(width * _HERO_ART_RATIO)
    try:
        loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
        loader.set_size(width, height)
        loader.write(svg.encode("utf-8"))
        loader.close()
        pixbuf = loader.get_pixbuf()
    except GLib.GError:
        # No SVG pixbuf loader on this system. the generic
        # camera icon fallback stays in place.
        return None
    if pixbuf is None:
        return None
    return texture_for_pixbuf(pixbuf)


@dataclass(frozen=True)
class CarrierHooks:
    """How the pane reads and registers a body's carrier.

    Attributes:
        carrier_for: Returns the carrier file registered for a body model.
        set_carrier: Registers a carrier file for a body.
        current_raf: Returns the RAF open in the main window.
    """

    carrier_for: Callable[[str], str | None]
    set_carrier: Callable[[str, str | None], str | None]
    current_raf: Callable[[], str | None]


@Gtk.Template(string=_UI)
class CameraPane(Gtk.Box):
    """Assign saved recipes to the connected body's banks and FS dial.

    The pane is view glue only: the dialog wires it to the library and
    the camera callbacks, collects the assignments for the transfer and
    owns the Transfer button.
    """

    __gtype_name__ = "GrawjiCameraPane"

    camera_image = Gtk.Template.Child()
    camera_header = Gtk.Template.Child()
    camera_stack = Gtk.Template.Child()
    camera_banks = Gtk.Template.Child()
    carrier_row = Gtk.Template.Child()
    carrier_current_button = Gtk.Template.Child()
    carrier_choose_button = Gtk.Template.Child()
    carrier_clear_button = Gtk.Template.Child()

    def __init__(self, **kwargs: object) -> None:
        """Build the empty pane."""
        super().__init__(**kwargs)
        self._library: RecipeLibrary | None = None
        self._caps: Any = None
        self._get_model: Callable[[], str | None] | None = None
        self._load_bank_names: (
            Callable[[Callable[[str | None, list[str]], None]], None] | None
        ) = None
        self._take_dragged: Callable[[], str | None] = lambda: None
        self._built_model: str | None = None
        self._banks: list[dict[str, Any]] = []
        self._bank_names_loaded = False
        self._bank_recipe: dict[int, str] = {}
        self._fs_cards: list[dict[str, Any]] = []
        self._fs_recipe: dict[int, str] = {}
        self._carrier: CarrierHooks | None = None
        self.carrier_choose_button.connect("clicked", self._on_choose_carrier)
        self.carrier_current_button.connect(
            "clicked", self._on_use_current_carrier
        )
        self.carrier_clear_button.connect("clicked", self._on_clear_carrier)

    def wire(
        self,
        *,
        library: RecipeLibrary,
        caps: Any,
        get_model: Callable[[], str | None] | None,
        load_bank_names: (
            Callable[[Callable[[str | None, list[str]], None]], None] | None
        ),
        take_dragged: Callable[[], str | None],
        carrier: CarrierHooks | None = None,
    ) -> None:
        """Connect the pane to the dialog's collaborators.

        Args:
            library: The saved-recipe store the drops resolve against.
            caps: The connected body's Capabilities for compat notes,
                or None to disable them.
            get_model: Returns the connected body's model, or None.
            load_bank_names: Reads current bank names off the main
                thread, or None.
            take_dragged: Returns and consumes the name of the recipe
                row currently being dragged, or None.
            carrier: How to read and register the body's carrier for
                cross-body conversion.
        """
        self._library = library
        self._caps = caps
        self._get_model = get_model
        self._load_bank_names = load_bank_names
        self._take_dragged = take_dragged
        self._carrier = carrier

    def refresh(self) -> bool:
        """Show the connected body's banks, or a placeholder.

        Returns:
            Whether the pane is writable.
        """
        model = self._get_model() if self._get_model else None
        if not model:
            self.camera_stack.set_visible_child_name("none")
            return False

        self.camera_stack.set_visible_child_name("banks")
        self._populate(model)
        if self._load_bank_names is not None:
            self._load_bank_names(self.set_bank_names)
        return True

    def _populate(self, model: str) -> None:
        """(Re)build the header and the bank/FS cards for model."""
        self._built_model = model
        self._banks.clear()
        self._fs_cards.clear()
        self._bank_recipe.clear()
        self._fs_recipe.clear()
        while (child := self.camera_banks.get_first_child()) is not None:
            self.camera_banks.remove(child)
        self.camera_header.set_label(f"{model} · custom banks")
        texture = _family_paintable(model)
        if texture is not None:
            self.camera_image.set_from_paintable(texture)
        self.camera_banks.append(self._section_heading("Custom banks"))
        for slot in range(capabilities_for_model(model).num_bank_slots):
            self.camera_banks.append(self._build_bank_card(slot))
        self._build_fs_section(model)
        self._refresh_carrier()

    def _refresh_carrier(self) -> None:
        """Show the body's carrier, and offer the open RAF when it fits."""
        model = self._built_model
        section = self.carrier_row.get_parent().get_parent()
        if model is None or self._carrier is None:
            section.set_visible(False)
            return
        section.set_visible(True)
        carrier = self._carrier.carrier_for(model)
        self.carrier_row.set_subtitle(
            Path(carrier).name if carrier else "No carrier"
        )
        self.carrier_row.remove_css_class("error")
        self.carrier_clear_button.set_visible(bool(carrier))
        current = self._carrier.current_raf()
        self.carrier_current_button.set_visible(
            bool(current) and current != carrier
        )

    def _apply_carrier(self, path: str | None) -> None:
        """Register path for the built body and show the outcome."""
        if self._built_model is None or self._carrier is None:
            return
        problem = self._carrier.set_carrier(self._built_model, path)
        if problem:
            self.carrier_row.set_subtitle(problem)
            self.carrier_row.add_css_class("error")
            return
        self._refresh_carrier()

    def _on_choose_carrier(self, _button: Any) -> None:
        """Pick a carrier RAF with the file chooser."""
        dialog = Gtk.FileDialog()
        rafs = Gtk.FileFilter()
        rafs.set_name("Fujifilm RAF")
        rafs.add_pattern("*.RAF")
        rafs.add_pattern("*.raf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(rafs)
        dialog.set_filters(filters)
        dialog.set_default_filter(rafs)
        dialog.open(self.get_root(), None, self._on_carrier_picked)

    def _on_carrier_picked(self, dialog: Any, result: Any) -> None:
        """Take the picked file."""
        try:
            picked = dialog.open_finish(result)
        except GLib.Error:
            return
        path = picked.get_path() if picked is not None else None
        if path:
            self._apply_carrier(path)

    def _on_use_current_carrier(self, _button: Any) -> None:
        """Register the RAF open in the main window as the carrier."""
        current = self._carrier.current_raf() if self._carrier else None
        if current:
            self._apply_carrier(current)

    def _on_clear_carrier(self, _button: Any) -> None:
        """Forget the body's carrier."""
        self._apply_carrier(None)

    def collect(self) -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
        """The bank recipes, bank names and FS recipes to transfer."""
        return (
            dict(self._bank_recipe),
            self._names_by_slot(),
            dict(self._fs_recipe),
        )

    def on_transfer_finished(self) -> None:
        """Refresh the pane after a transfer."""
        self._bank_recipe.clear()
        self._fs_recipe.clear()
        for card in (*self._banks, *self._fs_cards):
            card["assigned"].set_label("Drop a recipe here")
            card["assigned"].add_css_class("dim-label")
        if self._load_bank_names is not None:
            self._load_bank_names(self.set_bank_names)

    def set_bank_names(self, model: str | None, names: list[str]) -> None:
        """Fill the bank names, correcting the pane's model first."""
        if model and model != self._built_model:
            self._populate(model)
        if not names:
            return
        self._bank_names_loaded = True
        for bank, name in zip(self._banks, names, strict=False):
            bank["name_row"].set_visible(True)
            bank["name_label"].set_label(name)
            bank["loaded"] = name
            bank["dropped"] = None

    def _build_fs_section(self, model: str | None) -> None:
        """Add FS1-FSn dial-position cards if this body has an FS layout."""
        layout = fs_recipe.layout_for(model)
        if layout is None:
            return
        self.camera_banks.append(
            self._section_heading("Film-simulation dial (FS)")
        )
        for slot in range(layout.num_slots):
            self.camera_banks.append(self._build_fs_card(slot))

    def _section_heading(self, text: str) -> Gtk.Widget:
        """A small heading that separates the bank and FS card groups."""
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("heading")
        label.add_css_class("dim-label")
        label.set_margin_top(6)
        return label

    def _build_bank_card(self, slot: int) -> Gtk.Widget:
        """A drop-target card for bank C{slot+1} with a rename affordance."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("card")
        inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        card.append(inner)

        header = Gtk.Box(spacing=8)
        title = Gtk.Label(label=f"C{slot + 1}", xalign=0)
        title.add_css_class("heading")
        header.append(title)
        name_label = Gtk.Label(xalign=0, hexpand=True)
        name_label.add_css_class("dim-label")
        header.append(name_label)
        edit = Gtk.Button(
            icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER
        )
        edit.add_css_class("flat")
        edit.set_tooltip_text("Rename this bank")
        edit.connect("clicked", self._on_rename_bank, slot)
        header.append(edit)
        inner.append(header)

        assigned = Gtk.Label(label="Drop a recipe here", xalign=0)
        assigned.add_css_class("dim-label")
        assigned.set_wrap(True)
        inner.append(assigned)

        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_bank_drop, slot)
        card.add_controller(drop)

        self._banks.append(
            {
                "name_row": header,
                "name_label": name_label,
                "assigned": assigned,
                "loaded": "",
                "dropped": None,
            }
        )
        return card

    def _build_fs_card(self, slot: int) -> Gtk.Widget:
        """A drop-target card for FS dial position slot+1.

        FS positions are physical dial presets, not user-named banks, so
        the card has no name row or rename affordance.
        """
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("card")
        inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        card.append(inner)

        title = Gtk.Label(label=f"FS{slot + 1}", xalign=0)
        title.add_css_class("heading")
        inner.append(title)

        assigned = Gtk.Label(label="Drop a recipe here", xalign=0)
        assigned.add_css_class("dim-label")
        assigned.set_wrap(True)
        inner.append(assigned)

        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_fs_drop, slot)
        card.add_controller(drop)

        self._fs_cards.append({"assigned": assigned})
        return card

    def _compat_note(self, name: str) -> str:
        """A short compatibility suffix for a recipe on this body."""
        recipe = self._library.get(name) if self._library else None
        if recipe is None or self._caps is None:
            return ""
        verdict = compat.evaluate(recipe, self._caps)
        if verdict.level == compat.UNAVAILABLE:
            return "  (film sim N/A here)"
        if verdict.level == compat.DEGRADED:
            return f"  ({len(verdict.issues)} dropped)"
        return ""

    def _dropped_name(self, value: Any) -> str | None:
        """Resolve a drop payload to a saved recipe name, or None."""
        name = value if isinstance(value, str) else self._take_dragged()
        if (
            not name
            or self._library is None
            or self._library.get(name) is None
        ):
            return None
        return name

    def _on_bank_drop(
        self, _target: Any, value: Any, _x: float, _y: float, slot: int
    ) -> bool:
        """Assign the dropped recipe to bank slot."""
        name = self._dropped_name(value)
        if name is None:
            return False
        self._bank_recipe[slot] = name
        label = self._banks[slot]["assigned"]
        label.set_label(f"→ {name}{self._compat_note(name)}")
        label.remove_css_class("dim-label")
        self._name_after_recipe(slot, name)
        return True

    def _name_after_recipe(self, slot: int, name: str) -> None:
        """Give a bank the name of the recipe dropped on it."""
        if not self._bank_names_loaded:
            return
        bank = self._banks[slot]
        shown = bank["name_label"].get_text()
        if shown in (bank["loaded"], bank["dropped"]):
            bank["name_label"].set_label(name)
            bank["dropped"] = name

    def _on_fs_drop(
        self, _target: Any, value: Any, _x: float, _y: float, slot: int
    ) -> bool:
        """Assign the dropped recipe to FS dial position slot."""
        name = self._dropped_name(value)
        if name is None:
            return False
        self._fs_recipe[slot] = name
        label = self._fs_cards[slot]["assigned"]
        label.set_label(f"→ {name}{self._compat_note(name)}")
        label.remove_css_class("dim-label")
        return True

    def _on_rename_bank(self, _button: Any, slot: int) -> None:
        """Prompt for a new name for bank slot."""
        label = self._banks[slot]["name_label"]
        self._prompt(
            f"Rename bank C{slot + 1}",
            "New name",
            label.get_text(),
            label.set_label,
        )

    def _names_by_slot(self) -> dict[int, str]:
        """Bank names the user changed from the loaded ones."""
        out: dict[int, str] = {}
        for slot, bank in enumerate(self._banks):
            if not bank["name_row"].get_visible():
                continue
            text = bank["name_label"].get_text().strip()
            if text and text != bank["loaded"]:
                out[slot] = text
        return out

    def _prompt(
        self,
        heading: str,
        body: str,
        preset: str,
        done: Callable[[str], None],
    ) -> None:
        """Show a one-entry text dialog."""
        dialog = Adw.AlertDialog(heading=heading, body=body)
        entry = Gtk.Entry(text=preset)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d: Any, response: str) -> None:
            value = entry.get_text().strip()
            if response == "ok" and value and value != preset:
                done(value)

        dialog.connect("response", on_response)
        dialog.present(self)
