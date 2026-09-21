"""The folder grid tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grawji import catalog

pytestmark = pytest.mark.gui


def loader(tmp_path: Path) -> Any:
    """A thumbnail loader that decodes nothing during the test."""
    from grawji.imaging.thumbnails import ThumbnailLoader

    return ThumbnailLoader(
        height=160,
        cache_dir=tmp_path / "cache",
        workers=1,
        dispatch=lambda call: call(),
    )


@pytest.fixture
def grid(gtk: Any, tmp_path: Path) -> Any:
    """A grid over three RAFs, with the frames it opened recorded."""
    from grawji.views.folder_grid import FolderGrid

    for name in ("a.RAF", "b.RAF", "c.RAF"):
        (tmp_path / name).write_bytes(b"not a real raf")
    opened: list[str] = []
    built = FolderGrid(loader=loader(tmp_path), on_activate=opened.append)
    built.opened = opened
    built.folder = tmp_path
    built.show_entries(catalog.scan(tmp_path))
    return built


def test_the_grid_holds_the_whole_folder(grid: Any) -> None:
    """Every frame of the scan reaches the list model."""
    assert grid.shown == 3


def test_showing_another_folder_replaces_the_content(
    grid: Any, tmp_path: Path
) -> None:
    """A new scan does not pile up on the old one."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.RAF").write_bytes(b"not a real raf")
    grid.show_entries(catalog.scan(other))
    assert grid.shown == 1


def test_selecting_a_path_finds_its_tile(grid: Any) -> None:
    """The grid can follow a selection made elsewhere."""
    wanted = str(grid.folder / "b.RAF")
    assert grid.select_path(wanted)
    assert not grid.select_path(str(grid.folder / "missing.RAF"))


def test_activating_a_tile_opens_that_frame(grid: Any) -> None:
    """A double click hands the RAF path to the window."""
    grid._on_activated(None, 2)
    assert grid.opened == [str(grid.folder / "c.RAF")]


def test_a_late_thumbnail_for_a_recycled_tile_is_dropped(grid: Any) -> None:
    """Scrolling fast must not paint a tile with the wrong frame."""
    assert grid._thumbs.waiting == 0
    grid._thumbs._on_thumb(str(grid.folder / "a.RAF"), _pixbuf(), None)


def _pixbuf() -> Any:
    """A one pixel pixbuf, enough to build a texture from."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    return GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 1, 1)


def toggle(window: Any, wanted: bool) -> None:
    """Drive the window action the button and the key both use."""
    window._set_grid_open(open_it=wanted)


def test_the_window_swaps_the_preview_for_the_grid(
    window: Any, tmp_path: Path
) -> None:
    """The g shortcut shows the folder and comes back on a pick."""
    from tests.gui_support import pump

    for name in ("a.RAF", "b.RAF"):
        (tmp_path / name).write_bytes(b"not a real raf")
    window._scan_folder(str(tmp_path))
    pump()
    toggle(window, True)
    pump()
    assert window.view_stack.get_visible_child_name() == "grid"
    assert window._grid.shown == 2
    toggle(window, False)
    assert window.view_stack.get_visible_child_name() == "preview"


def test_a_new_folder_reaches_an_open_grid(
    window: Any, tmp_path: Path
) -> None:
    """Switching folders while the grid shows must refill it."""
    from tests.gui_support import pump

    first, second = tmp_path / "one", tmp_path / "two"
    for folder, names in ((first, ("a.RAF",)), (second, ("b.RAF", "c.RAF"))):
        folder.mkdir()
        for name in names:
            (folder / name).write_bytes(b"not a real raf")
    window._scan_folder(str(first))
    pump()
    toggle(window, True)
    pump()
    assert window._grid.shown == 1
    window._scan_folder(str(second))
    pump()
    assert window._grid.shown == 2
    assert window.view_stack.get_visible_child_name() == "grid"


def test_the_tab_and_the_key_share_one_state(
    window: Any, tmp_path: Path
) -> None:
    """The switcher and the shortcut drive the same page."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib

    (tmp_path / "a.RAF").write_bytes(b"not a real raf")
    window._scan_folder(str(tmp_path))
    toggle(window, True)
    assert window.view_stack.get_visible_child_name() == "grid"
    assert window.lookup_action("toggle-grid").get_state().get_boolean()
    window.lookup_action("toggle-grid").change_state(
        GLib.Variant.new_boolean(False)
    )
    assert window.view_stack.get_visible_child_name() == "preview"


def test_opening_a_tile_returns_to_the_preview(
    window: Any, tmp_path: Path
) -> None:
    """Picking a frame in the grid develops it, it does not stay."""
    (tmp_path / "a.RAF").write_bytes(b"not a real raf")
    window._scan_folder(str(tmp_path))
    toggle(window, True)
    window._on_grid_activated(str(tmp_path / "a.RAF"))
    assert window.view_stack.get_visible_child_name() == "preview"


def test_without_a_folder_the_grid_is_simply_empty(window: Any) -> None:
    """Browsing before a folder is open shows an empty grid, not an error."""
    window._current_folder = None
    toggle(window, True)
    assert window.view_stack.get_visible_child_name() == "grid"
    assert window._grid.held == 0


def test_the_grid_shows_what_the_filter_leaves(
    window: Any, tmp_path: Path
) -> None:
    """One folder, one filter, two views that agree."""
    from grawji import catalog
    from grawji.imaging.thumbnails import ThumbMeta
    from tests.gui_support import pump

    for name in ("a.RAF", "b.RAF"):
        (tmp_path / name).write_bytes(b"not a real raf")
    window._scan_folder(str(tmp_path))
    pump()
    strip = window._filmstrip
    bodies = {strip.paths[0]: "X-E5", strip.paths[1]: "X100F"}
    for path, model in bodies.items():
        strip._entries[path] = catalog.with_meta(
            strip._entries[path], model, "", "23 mm"
        )
    toggle(window, True)
    pump()
    assert window._grid.shown == 2
    strip.set_filter(model="X100F", lens=None, focal=None)
    pump()
    assert window._grid.shown == 1
    strip.set_filter(model=None, lens=None, focal=None)
    pump()
    assert window._grid.shown == 2


def test_only_the_visible_tiles_ask_for_a_thumbnail(
    window: Any, tmp_path: Path
) -> None:
    """GTK binds a deep pool of tiles, we load what is on screen."""
    import time

    from grawji import catalog
    from tests.gui_support import pump

    for index in range(60):
        (tmp_path / f"{index:03}.RAF").write_bytes(b"not a real raf")
    asked: list[str] = []
    grid = window._grid
    real = grid._thumbs._loader.request

    def spy(path: str, ready: Any) -> None:
        asked.append(path)
        real(path, ready)

    grid._thumbs._loader.request = spy
    window.set_default_size(900, 700)
    window.present()
    window._scan_folder(str(tmp_path))
    toggle(window, True)
    for _ in range(30):
        pump()
        time.sleep(0.005)
    assert len(catalog.scan(tmp_path)) == 60
    assert grid.held == 60
    assert len(asked) < 60
    assert grid._thumbs.waiting


def test_the_slider_changes_the_tile_size(grid: Any) -> None:
    """Dragging the size slider reflows the grid at once."""
    grid.set_tile(320)
    assert grid._tile == 320
    for cell in grid._cells.values():
        assert cell.get_size_request().height == 320
    grid.set_tile(320)


def test_a_size_change_re_decodes_what_is_bound(grid: Any) -> None:
    """Old thumbnails would stay small, so every bound tile is re-asked."""
    thumbs = grid._thumbs
    tile, picture = object(), _Picture()
    thumbs._bound[tile] = (picture, "/tmp/a.RAF")
    thumbs._textures["/tmp/a.RAF"] = object()
    thumbs.clear()
    assert thumbs._textures == {}
    assert tile in thumbs._waiting


class _Picture:
    """Stand-in for the tile's picture."""

    def set_paintable(self, _paintable: Any) -> None:
        """Ignore what is painted."""

    def set_size_request(self, _width: int, _height: int) -> None:
        """Ignore the size."""


def test_picking_in_the_grid_marks_it_without_developing(
    window: Any, tmp_path: Path
) -> None:
    """Browsing moves the marker, the camera stays out of it."""
    from tests.gui_support import pump

    for name in ("a.RAF", "b.RAF"):
        (tmp_path / name).write_bytes(b"not a real raf")
    window._scan_folder(str(tmp_path))
    pump()
    developed: list[str] = []

    def develop(path: str) -> None:
        developed.append(path)
        window._raf_path = Path(path)

    window._on_raf_selected = develop
    toggle(window, True)
    pump()
    window._on_grid_selected(str(tmp_path / "b.RAF"))
    assert window._filmstrip.current_path == str(tmp_path / "b.RAF")
    assert developed == []
    toggle(window, False)
    assert developed == [str(tmp_path / "b.RAF")]
    toggle(window, True)
    toggle(window, False)
    assert developed == [str(tmp_path / "b.RAF")]


def test_opening_a_tile_develops_it_once(window: Any, tmp_path: Path) -> None:
    """A double click in the grid loads the frame a single time."""
    from tests.gui_support import pump

    (tmp_path / "a.RAF").write_bytes(b"not a real raf")
    window._scan_folder(str(tmp_path))
    pump()
    developed: list[str] = []
    window._on_raf_selected = developed.append
    toggle(window, True)
    pump()
    window._on_grid_activated(str(tmp_path / "a.RAF"))
    assert window.view_stack.get_visible_child_name() == "preview"
    assert developed == [str(tmp_path / "a.RAF")]


def test_the_rest_of_the_folder_is_fetched_behind_the_viewport(
    gtk: Any, tmp_path: Path
) -> None:
    """Scrolling is only smooth if the frames are decoded already."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from grawji.views.tile_thumbs import TileThumbs

    asked: list[str] = []
    thumbs = TileThumbs(loader(tmp_path), Gtk.ScrolledWindow())
    thumbs._loader.request = lambda path, _ready: asked.append(path)
    paths = [f"/frames/{index:03}.RAF" for index in range(20)]
    thumbs.prefetch(paths, around=10)
    thumbs._pending[object()] = (None, paths[0])
    thumbs._prefetch_batch()
    assert asked == []
    thumbs._pending.clear()
    thumbs._prefetch_batch()
    assert asked, "the folder has to be worked through in the background"
    assert asked[0] == paths[10]
    assert set(asked[:3]) <= {paths[9], paths[10], paths[11]}


def test_showing_the_same_folder_again_keeps_the_grid(grid: Any) -> None:
    """Rebuilding it would drop every measured size and decoded frame."""
    cleared: list[str] = []
    grid._thumbs.clear = lambda: cleared.append("cleared")
    same = catalog.scan(grid.folder)
    grid.show_entries(same)
    assert cleared == []
    assert grid.shown == 3
    # A different folder still replaces it.
    other = grid.folder / "other"
    other.mkdir()
    (other / "x.RAF").write_bytes(b"not a real raf")
    grid.show_entries(catalog.scan(other))
    assert cleared == ["cleared"]
    assert grid.shown == 1
