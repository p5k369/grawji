# grawji

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: GPL-3.0-or-later](https://img.shields.io/github/license/p5k369/grawji)](LICENSE)
[![Tests](https://github.com/p5k369/grawji/actions/workflows/test.yml/badge.svg)](https://github.com/p5k369/grawji/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/p5k369/grawji/graph/badge.svg)](https://codecov.io/gh/p5k369/grawji)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/p5k369/grawji/main.svg)](https://results.pre-commit.ci/latest/github/p5k369/grawji/main)


GTK4 frontend for [rawji](https://github.com/pinpox/rawji). Develop Fujifilm
RAFs natively on Linux through the **real camera engine** (authentic film
simulations, identical to X RAW STUDIO).

The name is **g**(tk) + **rawji** (/dʒiː ˈrɔː dʒiː/).

<p align="center">
  <img src="docs/screenshot.png" alt="grawji main window: original + EXIF on the
  left, live preview in the centre, recipe controls on the right, filmstrip
  along the bottom" width="640">
</p>

## Features

- **Live preview** through the camera's own conversion engine, with
  histogram, clipping zebras, peek at the original, and split-compare.
- **Recipes**: full parameter control, saved library with hotkeys,
  community-text paste, FP1/FP2/FP3 exchange, try-them-all grid.
- **Crop, straighten and per-image exposure**, with auto level, kept in
  per-RAF sidecars.
- **Export** single images or whole folders at full resolution, with
  optional framing border and metadata.
- **Cull and organize** in the filmstrip: filter, select, copy, move, trash.
- **Experimental**: write recipes into the camera's custom banks over
  USB. Verified on the X100F, X-T3 and X-E5. Bodies sharing their
  write mechanism (X-Pro2, X-T2, X-T20, X-E3, X-T30 and the other
  X-Processor 5 models) should work but are untested. Not possible yet
  on the X-H1, the X-Processor 4 bodies from the X-Pro3 to the
  X-T30 II, and the GFX models before the GFX100 II. You can help
  enable your body: see
  [docs/enable-bank-writing.md](docs/enable-bank-writing.md).

## Warranty disclaimer

Fujifilm's official
[Camera Control SDK page](https://www.fujifilm-x.com/global/camera-control-sdk/)
states, verbatim:

> USING THIS SDK TO CONNECT TO OR CONTROL, ANY COMPATIBLE FUJIFILM CAMERA
> WILL VOID THE CAMERA'S LIMITED PRODUCT WARRANTY.

grawji and rawji do not use that SDK, but they talk to the camera over the
same USB protocol and are not licensed by Fujifilm.
[Reporting based on statements from Fujifilm](https://fujixweekly.com/2026/06/08/your-cameras-warranty-might-be-voided/)
says the policy extends to any non-licensed program connecting to the
camera, and that the camera records a marker each time one connects.
Consumer-protection law in your country may limit or override such terms,
but do not count on it: **use grawji at your own risk.**

## Install

First, put the camera in RAW-conversion USB mode, otherwise rawji cannot talk to it:

> **Set Up** → **Connection Setting** → **USB Mode** → **USB RAW CONV./BACKUP RESTORE**

| How | Install | Notes |
| --- | --- | --- |
| **Debian / Ubuntu** | `sudo apt install ./grawji_*_all.deb` | package from [Releases](https://github.com/p5k369/grawji/releases), pulls GTK4 and ships the USB rule |
| **Flatpak** | `flatpak install --user grawji.flatpak` | bundle from [Releases](https://github.com/p5k369/grawji/releases), everything included |
| **Gentoo** | `emerge media-gfx/grawji` | in [GURU](https://wiki.gentoo.org/wiki/Project:GURU), `~amd64`, pulls `dev-python/rawji` |
| **Nix** | `nix run github:p5k369/grawji` | flake, no clone needed. `nix build` gives `./result/bin/grawji` |
| **Source** | `make install` | system GTK4 and PyGObject first, see below |

JPEG XL and HEIF export need `cjxl` and libheif with its HEVC plugins. The
Flatpak carries them, the Debian package recommends them, everywhere else they
are your distribution's packages. Without them those formats are simply not
offered.

USB access: the Debian package installs a udev rule, and most distributions
grant non-root access anyway through `uaccess` or `plugdev`. If yours does not,
add a rule for the Fuji vendor id `0x04cb`.

<details>
<summary><b>From source, step-by-step</b></summary>

GTK4 and PyGObject come from your distribution, not pip: install the system
packages, then `make install`.

**1. System packages** (GTK4, libadwaita, PyGObject, the GExiv2 EXIF reader,
and the USB stack). Names vary by distro:

| Distro | Install |
| --- | --- |
| Debian / Ubuntu | `apt install git make python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gexiv2-0.10 libgtk-4-1 libusb-1.0-0` |
| Fedora | `dnf install git make python3-gobject gtk4 libadwaita gexiv2 libusb1` |
| Arch | `pacman -S git make python-gobject gtk4 libadwaita gexiv2 libusb` |
| openSUSE | `zypper install git make python3-gobject gtk4 libadwaita-1-0 typelib-1_0-GExiv2-0_10 libusb-1_0-0` |
| Gentoo | `emerge dev-vcs/git sys-devel/make dev-python/pygobject gui-libs/gtk:4 gui-libs/libadwaita media-libs/gexiv2 dev-python/pyusb virtual/libusb` |

**2. Clone and install** (`make install` builds a venv with
`--system-site-packages` so it can import the system GTK, fetches rawji from
git, installs grawji):

```sh
git clone https://github.com/p5k369/grawji
cd grawji
make install
make run        # or: .venv/bin/python -m grawji
```

</details>

## Credits

grawji stands entirely on [rawji](https://github.com/pinpox/rawji) by
**[pinpox](https://github.com/pinpox)**, who did the hard work of talking to
the camera's conversion engine over USB and exposing it as a clean Python
library. grawji is just a GTK4 face on top of that. Thank you. And if you
find grawji useful, please go star rawji.

The profile format was reverse-engineered by
**[petabyt](https://github.com/petabyt)**, whose
[fp](https://github.com/petabyt/fp) and
[libfuji](https://github.com/petabyt/libfuji) are the authoritative reference
for the camera's d185 conversion profile. grawji's parameter encodings (e.g.
noise reduction, processor capabilities) were verified against that work.

## Third-party code inside grawji

grawji's automatic perspective correction is **not grawji's work**. It is a
line-by-line Python port.

| What | Where in grawji | Ported from | License |
|---|---|---|---|
| Automatic perspective correction | `src/grawji/keystone.py` | [darktable](https://github.com/darktable-org/darktable), `src/iop/ashift.c`, originally written by Ulrich Pegelow | GPL-3.0-or-later |
| Nelder-Mead optimizer | `src/grawji/keystone.py`| [Michael F. Hutt](https://github.com/huttmf/nelder-mead), by way of darktable's `src/iop/ashift_nmsimplex.c` | MIT |

The method, the line weighting, the vanishing-point search and the crop
fitting are all darktable's. grawji only translates them into Python and
feeds them line segments from [lsdetect](https://github.com/p5k369/lsdetect).
If that feature is useful to you, the people to thank are the darktable developers.

The Nelder-Mead optimizer is third-party code within darktable as well. It
is by Michael F. Hutt, copyright (c) 1997-2011, under the MIT license. The
site his notice points to no longer resolves, his own copy lives at
[huttmf/nelder-mead](https://github.com/huttmf/nelder-mead).

## License

GPL-3.0-or-later.
