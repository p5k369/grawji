#!/usr/bin/env python3
"""Check the version is consistent across the places that carry it."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

METAINFO = Path("data/io.github.p5k369.grawji.metainfo.xml")


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
