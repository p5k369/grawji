"""Tests for the bundled icons."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "grawji"
ICONS = SRC / "ui" / "icons"
ACTIONS = ICONS / "hicolor" / "scalable" / "actions"
NAME = re.compile(r"grawji-[a-z-]+-symbolic")


def test_every_used_icon_is_bundled() -> None:
    """Each grawji icon the code or a template names has its file."""
    used = {
        name
        for path in SRC.rglob("*")
        if path.suffix in {".py", ".ui"}
        for name in NAME.findall(path.read_text(encoding="utf-8"))
    }
    bundled = {path.stem for path in ACTIONS.glob("*.svg")}
    assert used
    assert used <= bundled


def test_no_symbolic_icon_lies_flat() -> None:
    """Symbolic icons outside the theme tree are not recolored on GTK 4.14."""
    assert not list(ICONS.glob("*-symbolic.svg"))


def test_app_icon_is_bundled_for_the_about_dialog() -> None:
    """The About dialog's icon resolves from a source checkout too."""
    data = SRC.parents[1] / "data" / "io.github.p5k369.grawji.svg"
    bundled = ICONS / "hicolor" / "scalable" / "apps" / data.name
    assert bundled.read_bytes() == data.read_bytes()
    about = (SRC / "views" / "dialogs.py").read_text(encoding="utf-8")
    assert f'application_icon="{data.stem}"' in about
