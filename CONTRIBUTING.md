# Contributing to grawji

grawji is a GTK4 frontend for [rawji](https://github.com/pinpox/rawji) -
interactive Fujifilm RAF conversion through the camera's own engine over
USB. The ground truth is a physical camera, not a spec.

## Setup

Python 3.11+ and the system GTK4 stack (GTK4, libadwaita, PyGObject,
GExiv2). PyGObject comes from the system, so the venv needs access to it:

```sh
make dev
make dev RAWJI="-e ../rawji"
```

Run with `make run` or `python -m grawji` (`--verbose` for debug logging),
always with the venv activated - the hooks rely on it. `make lint`,
`make format` and `make test` wrap the usual tools.

## Quality gates

`pre-commit install` sets up both hook types: commits run formatting,
lint and strict mypy, pushes also run pytest. A push that reaches the
remote has passed every gate, so there is nothing to run by hand.

Direct commits to `main` are blocked. Branch and open a PR.

## Commit messages

Subjects follow [Conventional Commits](https://www.conventionalcommits.org).
This is not style preference: release-please reads them, and the type
alone decides what happens at release time.

| Type | Effect at release time |
|------|------------------------|
| `feat` | minor bump, listed under Features |
| `fix` | patch bump, listed under Bug Fixes |
| `perf` | listed under Performance |
| everything else | no release, no changelog entry |

So the type is a decision, not a label. Reserve `fix` for behaviour a
user could notice, and use `build` or `chore` for packaging, tooling and
workflow work.

Scope the subject when it helps (`fix(export):`, `feat(camera):`), and
write the body for someone who finds the commit in a year: what was
wrong, not what you typed.

## Code layout

- Pure modules live at the package root, are mypy-strict and unit-tested.
  Mock the rawji boundary.
- GTK view glue lives in `grawji/views/` and is exempt from the strictest
  mypy rules. `GDK_BACKEND=x11 pytest -m gui` builds the widgets under a
  virtual display, skipped without one. Deeper behavior is verified by
  running the app.
- Build static UI from the `.ui` templates in `src/grawji/ui/`, not
  imperatively in Python. I use
  [Cambalache](https://gitlab.gnome.org/jpu/cambalache).
- Docstrings (Google style, plain prose, no RST), not banner comments.
- Preview latency is the top priority: camera calls run on the worker
  thread, results return via `GLib.idle_add`, rapid changes are debounced.

## Camera protocol changes

The d185 profile blob is undocumented. Everything grawji writes was
verified against real hardware (see `docs/usb-capture.md`). To keep it
that way:

- **Patch, never rebuild.** grawji read-modify-writes only verified bytes
  of the camera's own profile.
- **New offsets need hardware proof:** a passing
  `scripts/verify_offsets.py` run against a connected body, with body and
  date recorded. Render-identical output means the camera ignored your
  bytes, which is a failure, not a success.
- Per-body support is data, not code: update `grawji/capabilities.py` and
  `docs/feature-matrix.md` together.

No hardware for the change you are proposing? Say so in the PR. It can
usually be verified for you, but unverified protocol claims are not
merged.

## License

GPL-3.0-or-later. Contributions are accepted under the same terms.
