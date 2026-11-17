"""The recipe manager's camera pane."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from importlib import resources
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk

from grawji.camera import compatibility as compat
from grawji.camera import fs_recipe
from grawji.camera.capabilities import capabilities_for_model
from grawji.recipes import RecipeLibrary
from grawji.views.bank_row import BankRow
from grawji.views.carrier_row import CarrierRow
from grawji.views.recipe_thumb import thumb_texture
from grawji.views.textures import texture_for_pixbuf

_UI = (
    resources.files("grawji")
    .joinpath("ui", "camera_pane.ui")
    .read_text(encoding="utf-8")
)

# Referencing the class registers GrawjiBankRow's thumbnail type.
_ = BankRow

# Camera-family hero
_HERO_PX = 36
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
    """How the pane reads and registers a body's carriers.

    Attributes:
        carriers_for: Returns a body's carrier files by dynamic range.
        add_carrier: Registers a RAF as a body's carrier under the
            dynamic range it was shot at, returning a refusal.
        forget_carrier: Forgets a body's carrier of a dynamic range.
        current_raf: Returns the RAF open in the main window.
    """

    carriers_for: Callable[[str], dict[int, str]]
    add_carrier: Callable[[str, str], str | None]
    forget_carrier: Callable[[str, int], None]
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
    transfer_button = Gtk.Template.Child()
    camera_stack = Gtk.Template.Child()
    bank_list = Gtk.Template.Child()
    fs_section = Gtk.Template.Child()
    fs_list = Gtk.Template.Child()
    carrier_section = Gtk.Template.Child()
    carrier_expander = Gtk.Template.Child()
    carrier_row_100 = Gtk.Template.Child()
    carrier_row_200 = Gtk.Template.Child()
    carrier_row_400 = Gtk.Template.Child()
    carrier_problem = Gtk.Template.Child()
    carrier_add_button = Gtk.Template.Child()
    carrier_current_button = Gtk.Template.Child()

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
        self._banks: list[BankRow] = []
        self._bank_names_loaded = False
        self._bank_recipe: dict[int, str] = {}
        self._fs_cards: list[BankRow] = []
        self._fs_recipe: dict[int, str] = {}
        self._carrier: CarrierHooks | None = None
        self.carrier_rows: dict[int, CarrierRow] = {
            100: self.carrier_row_100,
            200: self.carrier_row_200,
            400: self.carrier_row_400,
        }
        for dr, row in self.carrier_rows.items():
            row.clear_button.connect("clicked", self._on_clear_carrier, dr)
        self.carrier_add_button.connect("clicked", self._on_choose_carrier)
        self.carrier_current_button.connect(
            "clicked", self._on_use_current_carrier
        )

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
        self.transfer_button.set_visible(bool(model))
        if not model:
            self.camera_stack.set_visible_child_name("none")
            return False

        self.camera_stack.set_visible_child_name("banks")
        self._populate(model)
        if self._load_bank_names is not None:
            self._load_bank_names(self.set_bank_names)
        return True

    def _populate(self, model: str) -> None:
        """(Re)build the header and the bank/FS rows for model."""
        self._built_model = model
        self._bank_recipe.clear()
        self._fs_recipe.clear()
        for row in self._banks:
            self.bank_list.remove(row)
        for row in self._fs_cards:
            self.fs_list.remove(row)
        self._banks.clear()
        self._fs_cards.clear()
        self.camera_header.set_label(model)
        texture = _family_paintable(model)
        if texture is not None:
            self.camera_image.set_from_paintable(texture)
        for slot in range(capabilities_for_model(model).num_bank_slots):
            row = BankRow(f"C{slot + 1}")
            row.edit_button.connect("clicked", self._on_rename_bank, slot)
            row.clear_button.connect("clicked", self._on_unassign_bank, slot)
            self._add_drop(row, self._on_bank_drop, slot)
            self.bank_list.append(row)
            self._banks.append(row)
        self._build_fs_section(model)
        self._refresh_carrier()

    def _add_drop(
        self, row: BankRow, handler: Callable[..., bool], slot: int
    ) -> None:
        """Let a row take a dragged recipe."""
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", handler, slot)
        row.add_controller(drop)

    def _refresh_carrier(self) -> None:
        """Show each range's carrier, offering the open RAF when it is new."""
        model = self._built_model
        if model is None or self._carrier is None:
            self.carrier_section.set_visible(False)
            return
        self.carrier_section.set_visible(True)
        registered = self._carrier.carriers_for(model)
        for dr, row in self.carrier_rows.items():
            higher = [have for have in registered if have > dr]
            row.show(
                registered.get(dr),
                served_by=min(higher) if higher else None,
            )
        have = ", ".join(f"DR{dr}" for dr in sorted(registered))
        self.carrier_expander.set_subtitle(
            f"Carriers: {have}" if have else "No carrier yet"
        )
        current = self._carrier.current_raf()
        self.carrier_current_button.set_visible(
            bool(current) and current not in registered.values()
        )

    def _add_carrier(self, path: str) -> None:
        """Register path as a carrier of the built body, show the outcome."""
        if self._built_model is None or self._carrier is None:
            return
        problem = self._carrier.add_carrier(self._built_model, path)
        self.carrier_problem.set_label(problem or "")
        self.carrier_problem.set_visible(bool(problem))
        if problem:
            self.carrier_expander.set_expanded(True)
        if not problem:
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
            self._add_carrier(path)

    def _on_use_current_carrier(self, _button: Any) -> None:
        """Register the RAF open in the main window as a carrier."""
        current = self._carrier.current_raf() if self._carrier else None
        if current:
            self._add_carrier(current)

    def _on_clear_carrier(self, _button: Any, dr: int) -> None:
        """Forget the body's carrier of a dynamic range."""
        if self._built_model is None or self._carrier is None:
            return
        self._carrier.forget_carrier(self._built_model, dr)
        self.carrier_problem.set_visible(False)
        self._refresh_carrier()

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
        for row in (*self._banks, *self._fs_cards):
            row.clear_assignment()
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
            bank.set_bank_name(name)
            bank.loaded = name
            bank.dropped = None

    def _build_fs_section(self, model: str | None) -> None:
        """Add FS1-FSn dial rows if this body has an FS layout."""
        layout = fs_recipe.layout_for(model)
        self.fs_section.set_visible(layout is not None)
        if layout is None:
            return
        for slot in range(layout.num_slots):
            row = BankRow(f"FS{slot + 1}", renamable=False)
            row.clear_button.connect("clicked", self._on_unassign_fs, slot)
            self._add_drop(row, self._on_fs_drop, slot)
            self.fs_list.append(row)
            self._fs_cards.append(row)

    def _compat_note(self, name: str) -> str:
        """A short compatibility suffix for a recipe on this body."""
        recipe = self._library.get(name) if self._library else None
        if recipe is None or self._caps is None:
            return ""
        verdict = compat.evaluate(recipe, self._caps)
        if verdict.level == compat.UNAVAILABLE:
            return " · sim N/A"
        if verdict.level == compat.DEGRADED:
            return f" · {len(verdict.issues)} dropped"
        return ""

    def _thumb(self, name: str) -> Any:
        """A recipe's picture texture, or None."""
        if self._library is None:
            return None
        return thumb_texture(self._library, name)

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
        return name is not None and self.assign_bank(slot, name)

    def assign_bank(self, slot: int, name: str) -> bool:
        """Mark a saved recipe for bank slot, False if there is none."""
        if self._library is None or self._library.get(name) is None:
            return False
        if not 0 <= slot < len(self._banks):
            return False
        self._bank_recipe[slot] = name
        self._banks[slot].assign(
            name, self._compat_note(name), self._thumb(name)
        )
        self._name_after_recipe(slot, name)
        return True

    def assign_fs(self, slot: int, name: str) -> bool:
        """Mark a saved recipe for FS dial position slot."""
        if self._library is None or self._library.get(name) is None:
            return False
        if not 0 <= slot < len(self._fs_cards):
            return False
        self._fs_recipe[slot] = name
        self._fs_cards[slot].assign(
            name, self._compat_note(name), self._thumb(name)
        )
        return True

    def targets(self) -> list[tuple[str, Callable[[str], bool]]]:
        """The slots a recipe can go into, each with its assigner."""
        if self.camera_stack.get_visible_child_name() != "banks":
            return []
        banks: list[tuple[str, Callable[[str], bool]]] = [
            (f"C{slot + 1}", partial(self.assign_bank, slot))
            for slot in range(len(self._banks))
        ]
        dial: list[tuple[str, Callable[[str], bool]]] = [
            (f"FS{slot + 1}", partial(self.assign_fs, slot))
            for slot in range(len(self._fs_cards))
        ]
        return banks + dial

    def _on_unassign_bank(self, _button: Any, slot: int) -> None:
        """Take a pending recipe off a bank, and the name it gave."""
        self._bank_recipe.pop(slot, None)
        bank = self._banks[slot]
        if bank.dropped is not None and bank.bank_name == bank.dropped:
            bank.set_bank_name(bank.loaded)
        bank.dropped = None
        bank.clear_assignment()

    def _on_unassign_fs(self, _button: Any, slot: int) -> None:
        """Take a pending recipe off an FS dial position."""
        self._fs_recipe.pop(slot, None)
        self._fs_cards[slot].clear_assignment()

    def _name_after_recipe(self, slot: int, name: str) -> None:
        """Give a bank the name of the recipe dropped on it."""
        if not self._bank_names_loaded:
            return
        bank = self._banks[slot]
        if bank.bank_name in (bank.loaded, bank.dropped):
            bank.set_bank_name(name)
            bank.dropped = name

    def _on_fs_drop(
        self, _target: Any, value: Any, _x: float, _y: float, slot: int
    ) -> bool:
        """Assign the dropped recipe to FS dial position slot."""
        name = self._dropped_name(value)
        return name is not None and self.assign_fs(slot, name)

    def _on_rename_bank(self, _button: Any, slot: int) -> None:
        """Prompt for a new name for bank slot."""
        bank = self._banks[slot]
        self._prompt(
            f"Rename bank C{slot + 1}",
            "New name",
            bank.bank_name,
            bank.set_bank_name,
        )

    def _names_by_slot(self) -> dict[int, str]:
        """Bank names the user changed from the loaded ones."""
        out: dict[int, str] = {}
        for slot, bank in enumerate(self._banks):
            text = bank.bank_name.strip()
            if text and text != bank.loaded:
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
