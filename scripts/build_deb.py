#!/usr/bin/env python3
"""Build a .deb for Debian-derived distributions."""

from __future__ import annotations

import argparse
import email.utils
import gzip
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
APP_ID = "io.github.p5k369.grawji"
MAINTAINER_EMAIL = "patrick@p5k.org"
PROJECT_AUTHOR = "Patrick Zwerschke"
HOMEPAGE = "https://github.com/p5k369/grawji"


def _rfc_date() -> str:
    """Now, in the date format a Debian changelog entry requires."""
    return email.utils.formatdate(localtime=True)


DEBIAN_NAMES = {
    "pygobject": "python3-gi",
    "numpy": "python3-numpy",
    "pyusb": "python3-usb",
    "rawji": None,
}

EXTRA_DEPENDS = [
    "python3-gi-cairo",
    "gir1.2-gtk-4.0",
    "gir1.2-adw-1",
    "gir1.2-gexiv2-0.10",
    "gir1.2-gdkpixbuf-2.0",
]

# Optional formats, absent ones only shrink the export menu. The font is
# what GTK expects, a desktop has it and a minimal install does not.
RECOMMENDS = [
    "libjxl-tools",
    "libheif-plugin-x265",
    "libheif-plugin-libde265",
    "fonts-cantarell",
]

# Fujifilm bodies answer on this vendor id. uaccess hands the device to
# whoever is logged in at the seat, which is what lets grawji enumerate
# and reset the camera without root.
UDEV_RULE = """\
# Fujifilm cameras, so grawji can reach them without root.
SUBSYSTEM=="usb", ATTR{idVendor}=="04cb", TAG+="uaccess"
"""

ENTRY_POINT = """\
#!/usr/bin/python3
import sys

from grawji.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
"""


def _run(program: str, *arguments: str) -> None:
    """Run a build helper, resolved to an absolute path first."""
    found = shutil.which(program)
    if found is None:
        raise SystemExit(f"{program} not found")
    subprocess.run([found, *arguments], check=True)  # noqa: S603


def project() -> dict:
    """The parsed pyproject metadata."""
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def depends() -> list[str]:
    """The Depends line, derived from the declared dependencies."""
    names = []
    for item in project()["dependencies"]:
        bare = re.split(r"[\[<>=!@ ;]", item.strip(), maxsplit=1)[0]
        mapped = DEBIAN_NAMES.get(bare.lower().replace("_", "-"), "MISSING")
        if mapped == "MISSING":
            raise SystemExit(
                f"no Debian package known for {bare!r}, add it to DEBIAN_NAMES"
            )
        if mapped:
            names.append(mapped)
    return ["python3 (>= 3.11)", *sorted({*names, *EXTRA_DEPENDS})]


def _rawji_source(given: str | None, into: Path) -> Path:
    """A rawji checkout to vendor, cloned at the pin unless given."""
    if given:
        return Path(given).resolve()
    pin = re.search(
        r"rawji @ git\+(\S+?)@([0-9a-f]{40})",
        (ROOT / "pyproject.toml").read_text(),
    )
    if pin is None:
        raise SystemExit("pyproject.toml has no pinned rawji commit")
    url, commit = pin.groups()
    target = into / "rawji"
    _run("git", "clone", "-q", url, str(target))
    _run("git", "-C", str(target), "checkout", "-q", commit)
    return target


def _install_rawji(source: Path, site: Path) -> None:
    """Vendor rawji, which is neither on PyPI nor packaged anywhere."""
    package = source / "src" / "rawji"
    if not package.is_dir():
        raise SystemExit(f"no rawji package under {package}")
    shutil.copytree(
        package,
        site / "rawji",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _write_docs(tree: Path, version: str) -> None:
    """The changelog and copyright every package is expected to carry."""
    docs = tree / "usr" / "share" / "doc" / "grawji"
    docs.mkdir(parents=True)
    entry = (
        f"grawji ({version}) unstable; urgency=medium\n\n"
        "  * Release artifact built from the upstream tree; see the\n"
        "    project CHANGELOG for what changed.\n\n"
        f" -- {PROJECT_AUTHOR} <{MAINTAINER_EMAIL}>  {_rfc_date()}\n"
    )
    with gzip.open(docs / "changelog.gz", "wt", encoding="utf-8") as handle:
        handle.write(entry)
    (docs / "copyright").write_text(
        "Format: https://www.debian.org/doc/packaging-manuals/"
        "copyright-format/1.0/\n"
        "Upstream-Name: grawji\n"
        "Source: https://github.com/p5k369/grawji\n"
        "\n"
        "Files: *\n"
        f"Copyright: 2026 {PROJECT_AUTHOR}\n"
        "License: GPL-3.0-or-later\n"
        " This program is free software: you can redistribute it and/or\n"
        " modify it under the terms of the GNU General Public License as\n"
        " published by the Free Software Foundation, either version 3 of\n"
        " the License, or (at your option) any later version. The full\n"
        " text is in /usr/share/common-licenses/GPL-3.\n"
    )


def stage(tree: Path, rawji: str | None) -> None:
    """Lay out the file tree the package installs."""
    meta = project()
    site = tree / "usr" / "lib" / "python3" / "dist-packages"
    site.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "src" / "grawji", site / "grawji", ignore=ignore)
    with tempfile.TemporaryDirectory() as scratch:
        _install_rawji(_rawji_source(rawji, Path(scratch)), site)

    binary = tree / "usr" / "bin"
    binary.mkdir(parents=True)
    entry = binary / "grawji"
    entry.write_text(ENTRY_POINT)
    entry.chmod(0o755)

    share = tree / "usr" / "share"
    for target, name in (
        (share / "applications", f"{APP_ID}.desktop"),
        (share / "metainfo", f"{APP_ID}.metainfo.xml"),
        (share / "icons/hicolor/scalable/apps", f"{APP_ID}.svg"),
    ):
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy(DATA / name, target / name)

    _write_docs(tree, meta["version"])

    rules = tree / "usr" / "lib" / "udev" / "rules.d"
    rules.mkdir(parents=True)
    (rules / "60-grawji.rules").write_text(UDEV_RULE)

    control = tree / "DEBIAN"
    control.mkdir()
    size = sum(f.stat().st_size for f in tree.rglob("*") if f.is_file())
    (control / "control").write_text(
        f"""\
Package: grawji
Version: {meta["version"]}
Architecture: all
Maintainer: {meta["authors"][0]["name"]} <{MAINTAINER_EMAIL}>
Installed-Size: {size // 1024}
Depends: {", ".join(depends())}
Recommends: {", ".join(RECOMMENDS)}
Section: graphics
Priority: optional
Homepage: {HOMEPAGE}
Description: {meta["description"]}
 grawji drives the camera's own conversion engine over USB, so a RAF is
 developed by the body that shot it rather than by a third-party
 interpretation of its colours.
 .
 JPEG XL and HEIF export need the recommended packages. Without them
 those formats are simply not offered.
"""
    )


def main() -> int:
    """Build the package and report where it landed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rawji", help="use this checkout instead of the pin")
    parser.add_argument("--output", default="dist", help="output directory")
    args = parser.parse_args()

    if shutil.which("dpkg-deb") is None:
        raise SystemExit("dpkg-deb not found; run this on a Debian system")

    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    name = f"grawji_{project()['version']}_all.deb"
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch) / "pkg"
        tree.mkdir()
        stage(tree, args.rawji)
        _run(
            "dpkg-deb",
            "--build",
            "--root-owner-group",
            str(tree),
            str(out / name),
        )
    print(f"{out / name} ({(out / name).stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
