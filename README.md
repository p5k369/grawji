# grawji

GTK4 frontend for [rawji](https://github.com/pinpox/rawji) — develop Fujifilm
RAFs natively on Linux through the **real camera engine** (authentic film
simulations, identical to X RAW STUDIO).

The name is **g**(tk) + **rawji**. rawji is command-line only; grawji makes
*interactive* work on the look practical: set a recipe, see a live preview,
export — instead of typing CLI flags.

## Architecture

rawji is imported as a library. grawji is a GTK4 UI plus a thin adapter
(`grawji.core`) around rawji's public API, following a **load-once,
render-many** workflow:

- **Open RAF** (once, slow): `connect → send_raf → get_profile`
- **Change recipe** (often, fast — session + RAF stay open):
  `rmw_patch(base, recipe) → set_profile → trigger_conversion → wait_for_result`
- **Quit**: `disconnect`

> Order matters: `send_raf` *before* `get_profile`; profile-set *before*
> trigger. `send_raf` runs only on open, never per slider move.

Camera calls block for seconds, so they run on a worker thread with results
marshalled back via `GLib.idle_add`; only one camera op is in flight at a
time, and slider previews are debounced.

## Layout

```
src/grawji/
  __main__.py   # python -m grawji
  app.py        # Gtk.Application
  window.py     # main window
  core.py       # thin adapter around rawji (RMW, offsets)
  recipe.py     # pure recipe data model (testable)
  preview.py    # preview vs. full-res render pipeline
  ui/           # GTK .ui templates
tests/          # pure-module tests (recipe, adapter)
```

The value is in the pure modules; GTK widgets are view glue and are not
unit-tested.

## Development

PyGObject and GTK4 are **not** pip-installable cleanly — they come from your
distribution. Install them (plus the USB stack rawji uses) as system
packages, then build a venv that can *see* them with `--system-site-packages`:

```sh
python -m venv --system-site-packages .venv && . .venv/bin/activate
pip install -e ../rawji          # rawji is not on PyPI
pip install -e ".[dev]"          # finds system PyGObject; installs dev tools
pre-commit install
```

Without `--system-site-packages` the venv can't `import gi` and the app
won't start.

System packages by distribution (names vary, USB stack pulled in via rawji):

| Distro | Install |
| --- | --- |
| Debian / Ubuntu | `apt install python3-gi gir1.2-gtk-4.0 libgtk-4-1 libusb-1.0-0` |
| Fedora | `dnf install python3-gobject gtk4 libusb1` |
| Arch | `pacman -S python-gobject gtk4 libusb` |
| openSUSE | `zypper install python3-gobject gtk4 libusb-1_0-0` |
| Gentoo | `emerge dev-python/pygobject gui-libs/gtk:4 dev-python/pyusb virtual/libusb` |

### Packaging

`pyproject.toml` `[project]` metadata (PEP 621) is the single source of
truth, built with **hatchling** (PEP 517). No lockfile / `requirements.txt`
— distros read the metadata and substitute their own dependency versions.
Runtime deps (`PyGObject`, `rawji`) are declared abstractly so each distro's
packaging (Debian `pybuild`, Fedora `%pyproject` macros, Arch PKGBUILD,
Gentoo ebuild, …) maps them to its own system packages.

Tooling: **ruff**
(line-length 79, py311, google docstrings), **black** (`-l 79`), **mypy**
strict, **pytest**, all wired through **pre-commit** (with
`no-commit-to-branch` on `main`).

> USB: most distributions already grant non-root access to the camera via
> `uaccess`/`plugdev`. If yours doesn't, add a udev rule for the Fuji vendor
> id `0x04cb` — check first before adding one.

## Acknowledgements

grawji stands entirely on [rawji](https://github.com/pinpox/rawji) by
**[pinpox](https://github.com/pinpox)**, who did the hard work of talking to
the camera's conversion engine over USB and exposing it as a clean Python
library. grawji is just a GTK4 face on top of that. Thank you — and if you
find grawji useful, please go star rawji.

## License

GPL-3.0-or-later. grawji imports rawji (copyleft), so grawji itself must be
GPL-3.0-or-later.
