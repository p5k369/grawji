# grawji

GTK4 frontend for [rawji](https://github.com/pinpox/rawji) — develop Fujifilm
RAFs natively on Linux through the **real camera engine** (authentic film
simulations, identical to X RAW STUDIO).

The name is **g**(tk) + **rawji**. rawji is command-line only, grawji makes
*interactive* work on the look practical: set a recipe, see a live preview,
export.

<p align="center">
  <img src="docs/screenshot.png" alt="grawji main window: original + EXIF on the
  left, live preview in the centre, recipe controls on the right, filmstrip
  along the bottom" width="640">
</p>

## Features

- **Live preview** through the camera's own conversion engine — what you see
  is what the camera would write.
- **Full recipe control**: film simulation, white balance, dynamic range,
  highlights, shadows, color and sharpness.
- **Start from the image's own settings** (toggleable) or keep a sticky recipe
  and apply it across shots.
- **Presets**: save, apply, import and export named recipes.
- **Filmstrip** browser with EXIF info for the selected RAF.
- **Export** single images or batch-export a whole folder at full resolution.
- Keyboard shortcuts, pan/zoom with a darktable-style background, and a
  remembered window size and last folder.

## Architecture

rawji is imported as a library. grawji is a GTK4 UI plus a thin adapter
(`grawji.core`) around rawji's public API, following a **load-once,
render-many** workflow:

- **Open RAF** (once, slow): `connect → send_raf → get_profile`
- **Change recipe** (often, fast — session + RAF stay open):
  `rmw_patch(base, recipe) → set_profile → trigger_conversion → wait_for_result`
- **Quit**: `disconnect`

> Order matters: `send_raf` *before* `get_profile`, profile-set *before*
> trigger. `send_raf` runs only on open, never per slider move.

Camera calls block for seconds, so they run on a worker thread with results
marshalled back via `GLib.idle_add`. Only one camera op is in flight at a
time, and slider previews are debounced.


## Install

GTK4 and PyGObject come from your distribution, not pip. So: install the
system packages, then `make install`.

**1. System packages.** GTK4, libadwaita, PyGObject, the GExiv2 EXIF reader,
and the USB stack rawji uses. Package names vary by distro:

| Distro | Install |
| --- | --- |
| Debian / Ubuntu | `apt install git make python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gexiv2-0.10 libgtk-4-1 libusb-1.0-0` |
| Fedora | `dnf install git make python3-gobject gtk4 libadwaita gexiv2 libusb1` |
| Arch | `pacman -S git make python-gobject gtk4 libadwaita gexiv2 libusb` |
| openSUSE | `zypper install git make python3-gobject gtk4 libadwaita-1-0 typelib-1_0-GExiv2-0_10 libusb-1_0-0` |
| Gentoo | `emerge dev-vcs/git sys-devel/make dev-python/pygobject gui-libs/gtk:4 gui-libs/libadwaita media-libs/gexiv2 dev-python/pyusb virtual/libusb` |

**2. Install grawji.** Clone it and run `make install`. That builds a venv
(with `--system-site-packages` so it can import the system GTK), fetches rawji
from git, and installs grawji:

```sh
git clone https://github.com/p5k369/grawji
cd grawji
make install
```

**3. Set the camera's USB mode.** Before connecting, put the camera into RAW
conversion mode, or it enumerates as a card reader and rawji cannot talk to
it. On the camera:

> **Set Up** > **Connection Setting** > **USB Mode** >
> **USB RAW CONV./BACKUP RESTORE**

Then connect it over USB. This is the same mode Fujifilm X RAW STUDIO uses.

**4. Run:**

```sh
make run        # or: .venv/bin/python -m grawji
```

Open a folder of RAFs, pick one from the filmstrip, dial in a recipe, watch
the preview update, then **Export**.

> The RAF must come from the **connected camera body**. Fuji cameras only
> convert their own files, so a foreign RAF fails with PTP `0x2002`. The
> supported bodies are whatever rawji lists. To add yours, register its USB
> product id in your rawji checkout and grawji picks it up automatically.

> USB access: most distributions already grant non-root access to the camera
> via `uaccess` or `plugdev`. If yours does not, add a udev rule for the Fuji
> vendor id `0x04cb`. Check first before adding one.

## Development

`make dev` builds the venv with the dev extras and installs the pre-commit
hooks. To hack on rawji too (e.g. add a camera product id), clone it next to
grawji and override the dependency with an editable checkout:

```sh
make dev RAWJI="-e ../rawji"
```

`make lint` runs ruff + `mypy src tests`, `make format` formats, `make test`
runs pytest (line length 79). `pygobject-stubs` (a dev dependency) gives the
editor type hints for GTK and libadwaita.

## Credits

grawji stands entirely on [rawji](https://github.com/pinpox/rawji) by
**[pinpox](https://github.com/pinpox)**, who did the hard work of talking to
the camera's conversion engine over USB and exposing it as a clean Python
library. grawji is just a GTK4 face on top of that. Thank you. And if you
find grawji useful, please go star rawji.

## License

GPL-3.0-or-later. grawji imports rawji (copyleft), so grawji itself must be
GPL-3.0-or-later.
