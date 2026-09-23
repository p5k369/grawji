"""One folder, owned once and shown by every view of it."""

from __future__ import annotations

import os
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, GObject

from grawji import catalog, mainloop
from grawji.imaging.thumbnails import (
    ThumbMeta,
    ThumbnailLoader,
    remembered_facts,
)
from grawji.mainloop import Dispatch
from grawji.settings import cache_dir

_REFILTER_DELAY_MS = 150

Listener = Callable[[str, "str | None"], None]


class EntryItem(GObject.Object):
    """A catalog entry as a list item."""

    __gtype_name__ = "GrawjiEntryItem"

    def __init__(self, entry: catalog.Entry) -> None:
        """Wrap entry."""
        super().__init__()
        self.entry = entry


class FolderModel:
    """The entries, filter and metadata of the folder being shown."""

    def __init__(
        self,
        *,
        dispatch: Dispatch = mainloop.call,
        thumb_height: int = 110,
    ) -> None:
        """Create an empty model."""
        self.loader = ThumbnailLoader(
            height=thumb_height,
            cache_dir=cache_dir() / "thumbs",
            workers=max(1, (os.cpu_count() or 2) - 1),
            dispatch=dispatch,
        )
        self.store = Gio.ListStore.new(EntryItem)
        self.entries: dict[str, catalog.Entry] = {}
        self.paths: list[str] = []
        self.filter = catalog.Filter()
        self._listeners: list[Listener] = []
        self._refilter_id = 0

    def add_listener(self, listener: Listener) -> None:
        """Tell listener about folder, filter and entry changes."""
        self._listeners.append(listener)

    def _notify(self, reason: str, path: str | None = None) -> None:
        """Let every view react to a change."""
        for listener in self._listeners:
            listener(reason, path)

    def scan(self, folder: str) -> list[catalog.Entry]:
        """List folder, fill in what is known, sweep what is not."""
        entries = self._with_known_facts(catalog.scan(folder))
        paths = [entry.path for entry in entries]
        same = paths == self.paths
        trimmed = not same and set(paths) <= set(self.paths)
        self.entries = {entry.path: entry for entry in entries}
        self.paths = paths
        if same or trimmed:
            kept = set(paths)
            for position in reversed(range(self.store.get_n_items())):
                item = self.store.get_item(position)
                if item is None:
                    continue
                if item.entry.path in kept:
                    item.entry = self.entries.get(item.entry.path, item.entry)
                else:
                    self.store.remove(position)
        else:
            self.store.splice(
                0,
                self.store.get_n_items(),
                [EntryItem(entry) for entry in entries],
            )
        unknown = [entry.path for entry in entries if not entry.has_meta]
        if unknown:
            self.loader.sweep_meta(unknown, self._on_swept)
        if trimmed:
            self._notify("trimmed")
        elif not same:
            self._notify("folder")
        return entries

    def _with_known_facts(
        self, entries: list[catalog.Entry]
    ) -> list[catalog.Entry]:
        """Fill in what the thumbnail cache already knows."""
        facts = remembered_facts(cache_dir() / "thumbs")
        if not facts:
            return entries
        known = []
        for entry in entries:
            key = self.loader.cache_key(entry.path)
            fact = facts.get(key) if key else None
            if fact is None:
                known.append(entry)
                continue
            filled = catalog.with_meta(
                entry, fact.model, fact.lens, fact.focal
            )
            known.append(
                catalog.with_aspect(filled, fact.aspect)
                if fact.aspect
                else filled
            )
        return known

    def _on_swept(
        self, path: str, meta: ThumbMeta, aspect: float | None
    ) -> None:
        """Fold a swept frame's metadata into its entry."""
        entry = self.entries.get(path)
        if entry is None or entry.has_meta:
            return
        self.fold_meta(path, meta, aspect)

    def fold_meta(
        self, path: str, meta: ThumbMeta, aspect: float | None
    ) -> None:
        """Fold one frame's metadata and shape into its entry."""
        entry = self.entries.get(path)
        if entry is None:
            return
        entry = catalog.with_meta(entry, meta.model, meta.lens, meta.focal)
        if aspect:
            entry = catalog.with_aspect(entry, aspect)
        self.entries[path] = entry
        item = self.item_for(path)
        if item is not None:
            item.entry = entry
        self._notify("entry", path)
        if self.filter.is_active:
            self._refilter_soon()

    def item_for(self, path: str) -> EntryItem | None:
        """The list item holding one frame, if the folder has it."""
        for position in range(self.store.get_n_items()):
            item = self.store.get_item(position)
            if item is not None and item.entry.path == path:
                return item
        return None

    def entry_for(self, path: str) -> catalog.Entry | None:
        """The entry behind a path, as it stands right now."""
        return self.entries.get(path)

    def set_filter(self, entry_filter: catalog.Filter) -> None:
        """Narrow the folder to what the filter leaves, everywhere."""
        if entry_filter == self.filter:
            return
        self.filter = entry_filter
        self._notify("filter")

    def passes(self, entry: catalog.Entry) -> bool:
        """Whether a frame survives the filter, judged on live data."""
        live = self.entries.get(entry.path, entry)
        return self.filter.matches(live)

    def _refilter_soon(self) -> None:
        """Judge the folder again once the frames stop arriving."""
        if self._refilter_id:
            return
        self._refilter_id = GLib.timeout_add(
            _REFILTER_DELAY_MS, self.refilter_now
        )

    def refilter_now(self) -> bool:
        """Re-judge the folder in every view of it."""
        self._refilter_id = 0
        self._notify("filter")
        return GLib.SOURCE_REMOVE

    def known_models(self) -> list[str]:
        """Camera models present in the folder."""
        return catalog.cameras(self.entries.values())

    def known_lenses(self) -> list[str]:
        """Lens models present in the folder."""
        return catalog.lenses(self.entries.values())

    def known_focals(self) -> list[str]:
        """Focal lengths present in the folder."""
        return catalog.focal_labels(self.entries.values())
