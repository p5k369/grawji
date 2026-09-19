"""Guard that every packaging declares the same runtime dependencies."""

from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).parent.parent
FLATPAK = ROOT / "flatpak" / "io.github.p5k369.grawji.yaml"
FLAKE = ROOT / "flake.nix"
BUILD_DEB = ROOT / "scripts" / "build_deb.py"

# Import name to the spelling a packaging uses for it.
_ALIASES = {"pygobject3": "pygobject"}

# Dependencies a packaging gets from its platform instead of declaring:
_FROM_PLATFORM = {"flatpak": {"pygobject"}}


def _normalise(name: str) -> str:
    """Reduce a dependency to a comparable bare name."""
    bare = re.split(r"[\[<>=!@ ;]", name.strip(), maxsplit=1)[0]
    bare = bare.strip().lower().replace("_", "-")
    return _ALIASES.get(bare, bare)


def pyproject_dependencies() -> set[str]:
    """The runtime dependencies grawji declares for itself."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return {_normalise(item) for item in data["project"]["dependencies"]}


def flatpak_dependencies() -> set[str]:
    """The Python modules the Flatpak manifest builds."""
    names = re.findall(
        r"^\s*-\s*name:\s*(\S+)", FLATPAK.read_text(), re.MULTILINE
    )
    return {
        _normalise(name.removeprefix("python3-"))
        for name in names
        # Build-time only, never imported by the application.
        if name != "python3-hatchling"
    }


def flake_dependencies() -> set[str]:
    """The dependencies the Nix flake gives grawji itself."""
    text = FLAKE.read_text()
    start = text.index('pname = "grawji"')
    block = re.search(r"dependencies = \[(.*?)\];", text[start:], re.DOTALL)
    assert block is not None, "flake.nix has no dependencies for grawji"
    items = re.findall(r"[\w.]+", block.group(1))
    return {
        _normalise(item.removeprefix("python.pkgs."))
        for item in items
        if item.strip()
    }


def build_deb() -> ModuleType:
    """The Debian packaging script, loaded as a module."""
    spec = importlib.util.spec_from_file_location("build_deb", BUILD_DEB)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def deb_dependencies() -> set[str]:
    """The dependencies the Debian package knows a name for."""
    return {_normalise(name) for name in build_deb().DEBIAN_NAMES}


def test_flatpak_declares_every_runtime_dependency():
    """A dependency added to pyproject must reach the Flatpak too."""
    wanted = pyproject_dependencies() - _FROM_PLATFORM["flatpak"]
    missing = wanted - flatpak_dependencies()
    assert not missing, f"missing from the Flatpak manifest: {sorted(missing)}"


def test_flake_declares_every_runtime_dependency():
    """A dependency added to pyproject must reach the Nix flake too."""
    missing = pyproject_dependencies() - flake_dependencies()
    assert not missing, f"missing from flake.nix: {sorted(missing)}"


def test_the_comparison_actually_sees_the_dependencies():
    """Guard the parsers: a silent empty set would pass everything."""
    assert pyproject_dependencies() >= {"numpy", "pyusb", "rawji"}
    assert flatpak_dependencies() >= {"numpy", "pyusb", "rawji"}
    assert flake_dependencies() >= {"numpy", "pyusb", "rawji"}
    assert deb_dependencies() >= {"numpy", "pyusb", "rawji"}


def test_deb_names_every_runtime_dependency():
    """A dependency added to pyproject must reach the Debian package."""
    missing = pyproject_dependencies() - deb_dependencies()
    assert not missing, f"missing from DEBIAN_NAMES: {sorted(missing)}"


def test_the_deb_depends_line_resolves():
    """Building the Depends line raises on anything unmapped."""
    depends = build_deb().depends()
    assert "python3-numpy" in depends
    assert "python3-gi" in depends
