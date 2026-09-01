"""The recipe manager's camera pane: bank and FS-dial recipe drops."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GdkPixbuf, GLib, GObject, Gtk

from grawji.camera import compatibility as compat
from grawji.camera import fs_recipe
from grawji.imaging.render import texture_for_pixbuf
from grawji.recipes import RecipeLibrary

_UI = (
    resources.files("grawji")
    .joinpath("ui", "camera_pane.ui")
    .read_text(encoding="utf-8")
)

# Every supported body has seven C1-C7 custom banks.
_BANK_COUNT = 7
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
    camera_none_page = Gtk.Template.Child()

    def __init__(self, **kwargs: object) -> None:
        """Build the empty pane."""
        super().__init__(**kwargs)
        self._library: RecipeLibrary | None = None
        self._caps: Any = None
        self._get_model: Callable[[], str | None] | None = None
        self._load_bank_names: (
            Callable[[Callable[[list[str]], None]], None] | None
        ) = None
        self._take_dragged: Callable[[], str | None] = lambda: None
        self._banks: list[dict[str, Any]] = []
        self._bank_names_loaded = False
        self._bank_recipe: dict[int, str] = {}
        self._fs_cards: list[dict[str, Any]] = []
        self._fs_recipe: dict[int, str] = {}

    def wire(
        self,
        *,
        library: RecipeLibrary,
        caps: Any,
        get_model: Callable[[], str | None] | None,
        load_bank_names: Callable[[Callable[[list[str]], None]], None] | None,
        take_dragged: Callable[[], str | None],
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
        """
        self._library = library
        self._caps = caps
        self._get_model = get_model
        self._load_bank_names = load_bank_names
        self._take_dragged = take_dragged

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
        self.camera_header.set_label(f"{model} · custom banks")
        texture = _family_paintable(model)
        if texture is not None:
            self.camera_image.set_from_paintable(texture)
        self.camera_banks.append(self._section_heading("Custom banks"))
        for slot in range(_BANK_COUNT):
            self.camera_banks.append(self._build_bank_card(slot))
        self._build_fs_section(model)
        if self._load_bank_names is not None:
            self._load_bank_names(self.set_bank_names)
        return True

    def collect(self) -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
        """The bank recipes, bank names and FS recipes to transfer."""
        return (
            dict(self._bank_recipe),
            self._transfer_names(),
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

    def set_bank_names(self, names: list[str]) -> None:
        """Reveal each bank's name row and fill it from the camera."""
        if not names:
            return
        self._bank_names_loaded = True
        for bank, name in zip(self._banks, names, strict=False):
            bank["name_row"].set_visible(True)
            bank["name_label"].set_label(name)
            bank["loaded"] = name

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
        return True

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

    def _transfer_names(self) -> dict[int, str]:
        """Renames, plus default names for drops into unnamed banks."""
        names = self._names_by_slot()
        if not self._bank_names_loaded:
            return names
        for slot, recipe_name in self._bank_recipe.items():
            if slot not in names and not self._banks[slot]["loaded"]:
                names[slot] = recipe_name
        return names

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
