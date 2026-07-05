# Contributing to grawji

grawji is a GTK4 frontend for [rawji](https://github.com/pinpox/rawji) -
interactive Fujifilm RAF conversion through the camera's own engine over
USB. The ground truth is a physical camera, not a spec.

## Development setup

You need Python 3.11+ and the system GTK4 stack (GTK4, libadwaita,
PyGObject, GExiv2). PyGObject comes from the system, so create the venv
with access to it:

```sh
python -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e .[dev]
pip install -e ../rawji   # rawji is not on PyPI, clone it next to grawji
pre-commit install
```

Run the app with `python -m grawji` (add `--verbose` for debug logging),
and always work with the venv activated - the hooks rely on it.

## Quality gates

`pre-commit install` sets up both hook types: commits run formatting,
lint and strict mypy; pushes also run pytest. A push that reaches the
remote has passed all gates - nothing to run by hand
(`pre-commit run --all-files` and `pytest` work on demand).

Direct commits to `main` are blocked. Branch and open a PR. Commit
subjects follow the conventional-commit style in `git log`.

## Code layout and conventions

- Pure modules live at the package root and are mypy-strict and
  unit-tested. Mock the rawji boundary in tests.
- GTK view glue lives in `grawji/views/`, exempt from the strictest mypy
  rules. `gui`-marked smoke tests build the widgets under a virtual
  display. Run them with `GDK_BACKEND=x11 pytest -m gui` (needs the `dev`
  extra's `pytest-xvfb`). They skip without a display. Deeper behaviour is
  verified by running the app.
- Build static UI from the `.ui` templates in `src/grawji/ui/`, not
  imperatively in Python. I use
  [Cambalache](https://gitlab.gnome.org/jpu/cambalache)
  (`src/grawji/ui/grawji.cmb`).
- Docstrings (Google style, plain prose - no RST), not banner comments.
- Preview latency is the top priority: camera calls run on the worker
  thread, results return via `GLib.idle_add`, rapid changes are
  debounced.

## Camera protocol changes

The d185 profile blob is undocumented, everything grawji writes was
verified against real hardware (see `docs/usb-capture.md` for how).
To keep it that way:

- **Patch, never rebuild.** grawji read-modify-writes only verified
  bytes of the camera's own profile.
- **New offsets need hardware proof:** a passing
  `scripts/verify_offsets.py` run against a connected body, with body
  and date recorded. Render-identical output means the camera ignored
  your bytes - a failure, not a success.
- Per-body support is data, not code: update `grawji/capabilities.py`
  and `docs/feature-matrix.md` together.
- Testing needs a Fuji body in "USB RAW CONV./BACKUP RESTORE" mode and
  RAFs shot by that body (foreign RAFs fail with PTP error 0x2002).

No hardware for the change you are proposing? Say so in the PR - it can
usually be verified for you, but unverified protocol claims are not
merged.

## License

grawji is GPL-3.0-or-later; contributions are accepted under the same
terms.
