"""A lazy-loading folder tree for picking a folder of RAFs."""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gio, GLib, GObject, Gtk

_ATTRS = "standard::display-name,standard::type,standard::is-hidden"
_REVEAL_MAX_ATTEMPTS = 20


class _FolderItem(GObject.Object):
    """A single folder in the tree: its GFile and a display name."""

    __gtype_name__ = "GrawjiFolderItem"

    name = GObject.Property(type=str, default="")

    def __init__(
        self, file: Gio.File, name: str, *, is_bookmark: bool = False
    ) -> None:
        """Wrap a GFile with the label to show for it."""
        super().__init__()
        self.file = file
        self.name = name
        self.is_bookmark = is_bookmark


class FolderTree(Gtk.ScrolledWindow):
    """A filesystem folder tree; selecting a folder calls on_select.

    Shows directories only (hidden ones omitted), rooted at the home folder
    and the filesystem root so cards mounted outside home are reachable.
    """

    def __init__(
        self,
        *,
        on_select: Callable[[str], None],
        bookmarks: list[str] | None = None,
        on_bookmarks_changed: Callable[[list[str]], None] | None = None,
    ) -> None:
        """Create the tree.

        Args:
            on_select: Called with a folder path when one is selected.
            bookmarks: Folder paths pinned above the filesystem roots.
            on_bookmarks_changed: Called with the new bookmark list after
                the user adds or removes one, so it can be persisted.
        """
        super().__init__()
        self._on_select = on_select
        self._bookmarks = list(bookmarks or [])
        self._on_bookmarks_changed = on_bookmarks_changed
        self._launcher: Any = None
        self.add_css_class("folder-tree")
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self._dir_filter = Gtk.CustomFilter.new(self._is_visible_dir)
        self._name_sorter = Gtk.StringSorter.new(
            Gtk.PropertyExpression.new(_FolderItem, None, "name")
        )
        self._name_sorter.set_ignore_case(True)

        self._roots = Gio.ListStore.new(_FolderItem)
        for path in self._bookmarks:
            self._roots.append(self._bookmark_item(path))
        self._roots.append(
            _FolderItem(Gio.File.new_for_path(str(Path.home())), "Home")
        )
        self._roots.append(
            _FolderItem(Gio.File.new_for_path("/"), "Filesystem")
        )

        self._tree = Gtk.TreeListModel.new(
            self._roots,
            passthrough=False,
            autoexpand=False,
            create_func=self._children_of,
        )
        self._selection = Gtk.SingleSelection(model=self._tree)
        self._selection.set_autoselect(False)
        self._selection.set_can_unselect(True)
        self._selection.connect("notify::selected", self._on_selected)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)

        listview = Gtk.ListView(model=self._selection, factory=factory)
        listview.connect("activate", self._on_row_activated)
        self.set_child(listview)

    def reveal_path(self, target: str) -> None:
        """Expand the tree to target and select it (if it still exists).

        Walks from the relevant root down to the folder, expanding each
        level and waiting briefly for its (async) children to load.
        """
        target_path = Path(target)
        try:
            target_path.relative_to(Path.home())
            root = Path.home()
        except ValueError:
            root = Path("/")
        chain: list[Path] = []
        node = target_path
        while True:
            chain.append(node)
            if node in (root, node.parent):
                break
            node = node.parent
        chain.reverse()
        self._reveal_chain(chain, 0, 0)

    def _reveal_chain(
        self, chain: list[Path], index: int, attempt: int
    ) -> bool:
        """Expand one level of chain, then schedule the next (async)."""
        position, row = self._find_row(chain[index])
        if row is None:
            if attempt < _REVEAL_MAX_ATTEMPTS:  # children still loading
                GLib.timeout_add(
                    50, self._reveal_chain, chain, index, attempt + 1
                )
            return False
        if index == len(chain) - 1:
            self._selection.set_selected(position)
            return False
        row.set_expanded(True)
        GLib.timeout_add(50, self._reveal_chain, chain, index + 1, 0)
        return False

    def _on_row_activated(self, _list: Gtk.ListView, position: int) -> None:
        """Toggle a folder's expansion on double-click or Enter."""
        row = self._selection.get_item(position)
        if row is not None and row.is_expandable():
            row.set_expanded(not row.get_expanded())

    def _find_row(self, path: Path) -> tuple[int, Any]:
        """Return the (index, TreeListRow) for path in the flattened tree."""
        wanted = str(path)
        for i in range(self._tree.get_n_items()):
            row = self._tree.get_item(i)
            item = row.get_item()
            if item is not None and item.file.get_path() == wanted:
                return i, row
        return -1, None

    @staticmethod
    def _is_visible_dir(info: Gio.FileInfo) -> bool:
        """Whether a listing entry is a non-hidden directory."""
        return (
            info.get_file_type() == Gio.FileType.DIRECTORY
            and not info.get_is_hidden()
        )

    def _dir_model(self, folder: Gio.File) -> Gio.ListModel:
        """Build a name-sorted, directories-only model of folder's children."""
        listing = Gtk.DirectoryList.new(_ATTRS, folder)
        dirs = Gtk.FilterListModel.new(listing, self._dir_filter)
        items = Gtk.MapListModel.new(dirs, self._to_item)
        return Gtk.SortListModel.new(items, self._name_sorter)

    @staticmethod
    def _to_item(info: Gio.FileInfo) -> _FolderItem:
        """Map a directory listing entry to a _FolderItem."""
        file = info.get_attribute_object("standard::file")
        return _FolderItem(file, info.get_display_name())

    def _children_of(self, item: _FolderItem) -> Gio.ListModel | None:
        """Return the child folders of item, or None if it has none.

        Returning None for a leaf folder hides its (useless) expander.
        """
        if not self._has_subdir(item.file):
            return None
        return self._dir_model(item.file)

    @staticmethod
    def _has_subdir(folder: Gio.File) -> bool:
        """Whether folder contains at least one non-hidden subdirectory."""
        path = folder.get_path()
        if path is None:
            return False
        try:
            with os.scandir(path) as entries:
                return any(
                    not e.name.startswith(".")
                    and e.is_dir(follow_symlinks=False)
                    for e in entries
                )
        except OSError:
            return False

    def _on_setup(self, _factory: Any, item: Gtk.ListItem) -> None:
        """Build a tree row: an expander around a folder icon and label."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(Gtk.Image.new_from_icon_name("folder-symbolic"))
        box.append(Gtk.Label(xalign=0.0))
        expander = Gtk.TreeExpander()
        expander.set_child(box)
        item.set_child(expander)

        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", self._on_row_secondary, item)
        expander.add_controller(secondary)

    def _on_row_secondary(
        self,
        gesture: Gtk.GestureClick,
        _n: int,
        x: float,
        y: float,
        item: Gtk.ListItem,
    ) -> None:
        """Show a context menu for the right-clicked folder."""
        row = item.get_item()
        if row is None:
            return
        folder_item = row.get_item()
        folder = folder_item.file
        path = folder.get_path() or ""

        actions = Gio.SimpleActionGroup()

        def add(name: str, label: str, callback: Callable[[], None]) -> None:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a: callback())
            actions.add_action(action)
            menu.append(label, f"ctx.{name}")

        menu = Gio.Menu()
        if folder_item.is_bookmark:
            add(
                "remove-bookmark",
                "Remove Bookmark",
                partial(self._remove_bookmark, path),
            )
        elif path and path not in self._bookmarks:
            add(
                "add-bookmark",
                "Add Bookmark",
                partial(self._add_bookmark, path),
            )
        add(
            "open-files",
            "Open File Browser Here",
            partial(self._open_file_manager, folder),
        )

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.insert_action_group("ctx", actions)
        popover.set_has_arrow(False)
        popover.set_parent(gesture.get_widget())
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _bookmark_item(self, path: str) -> _FolderItem:
        """Build the pinned tree item for a bookmarked path."""
        name = Path(path).name or path
        return _FolderItem(Gio.File.new_for_path(path), name, is_bookmark=True)

    def _add_bookmark(self, path: str) -> None:
        """Pin path above the filesystem roots and persist the list."""
        if path in self._bookmarks:
            return
        self._roots.insert(len(self._bookmarks), self._bookmark_item(path))
        self._bookmarks.append(path)
        self._notify_bookmarks()

    def _remove_bookmark(self, path: str) -> None:
        """Unpin path and persist the list."""
        if path not in self._bookmarks:
            return
        index = self._bookmarks.index(path)
        self._roots.remove(index)
        self._bookmarks.remove(path)
        self._notify_bookmarks()

    def _notify_bookmarks(self) -> None:
        """Hand the current bookmark list to the persistence callback."""
        if self._on_bookmarks_changed is not None:
            self._on_bookmarks_changed(list(self._bookmarks))

    def _open_file_manager(self, folder: Gio.File) -> None:
        """Open the folder in the system file manager (via the portal)."""
        # Held on the instance so the async launch is not GC'd mid-flight.
        self._launcher = Gtk.FileLauncher.new(folder)
        self._launcher.launch(self.get_root(), None, None)

    @staticmethod
    def _on_bind(_factory: Any, item: Gtk.ListItem) -> None:
        """Bind the row's folder name, icon and expander."""
        row = item.get_item()
        expander = item.get_child()
        expander.set_list_row(row)
        folder_item = row.get_item()
        box = expander.get_child()
        icon = box.get_first_child()
        icon.set_from_icon_name(
            "user-bookmarks-symbolic"
            if folder_item.is_bookmark
            else "folder-symbolic"
        )
        box.get_last_child().set_label(folder_item.name)

    def _on_selected(self, selection: Gtk.SingleSelection, _p: Any) -> None:
        """Notify the listener when a folder is selected."""
        row = selection.get_selected_item()
        if row is None:
            return
        path = row.get_item().file.get_path()
        if path:
            self._on_select(path)
