#!/usr/bin/env python3
"""Check the version is consistent across the places that carry it."""

from __future__ import annotations

import argparse
import datetime
import re
import sys
import tomllib
from pathlib import Path

METAINFO = Path("data/io.github.p5k369.grawji.metainfo.xml")


def _release_errors(release_tag: str) -> list[str]:
    """Return the problems that make the metainfo unfit to release."""
    text = METAINFO.read_text()
    match = re.search(r"<release\b[^>]*>", text)
    if match is None:
        return ["metainfo has no <release> entry"]
    entry = match.group(0)
    errors = []
    if 'type="development"' in entry:
        errors.append(
            'metainfo release is marked type="development"; '
            "remove the marker to publish"
        )
    today = datetime.datetime.now(tz=datetime.UTC).date()
    date = re.search(r'date="([^"]+)"', entry)
    try:
        found = datetime.date.fromisoformat(date.group(1)) if date else None
    except ValueError:
        found = None
    # A day of slack absorbs the releaser's timezone vs the runner's UTC.
    if found is None or abs((found - today).days) > 1:
        errors.append(
            f"metainfo release date is "
            f"{date.group(1) if date else 'missing'}, but {release_tag} "
            f"is being released today ({today}); update the date"
        )
    return errors


def main() -> int:
    """Compare the versions and report a mismatch as a non-zero exit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="git tag to also require (e.g. v0.1.0)")
    args = parser.parse_args()

    data = tomllib.loads(Path("pyproject.toml").read_text())
    match = re.search(r'<release version="([^"]+)"', METAINFO.read_text())

    versions = {
        "pyproject": data["project"]["version"],
        "metainfo": match.group(1) if match else None,
    }
    if args.tag is not None:
        versions["tag"] = args.tag.lstrip("v")

    print(", ".join(f"{name}={value}" for name, value in versions.items()))
    if len(set(versions.values())) != 1:
        print("version mismatch: bump them together", file=sys.stderr)
        return 1

    if args.tag is not None:
        errors = _release_errors(args.tag)
        for error in errors:
            print(error, file=sys.stderr)
        if errors:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
