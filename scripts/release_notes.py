#!/usr/bin/env python3
"""Render GitHub release notes for a tag from the AppStream metainfo."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

METAINFO = Path("data/io.github.p5k369.grawji.metainfo.xml")


def _render(description: ET.Element | None) -> list[str]:
    """Turn a <description> element into Markdown lines."""
    lines: list[str] = []
    if description is None:
        return lines
    for child in description:
        if child.tag == "p" and child.text:
            lines.append(" ".join(child.text.split()))
            lines.append("")
        elif child.tag == "ul":
            for item in child.findall("li"):
                text = " ".join((item.text or "").split())
                if text:
                    lines.append(f"- {text}")
            lines.append("")
    return lines


def main() -> int:
    """Print the release notes for --tag, or exit non-zero on error."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="e.g. v0.3.0")
    args = parser.parse_args()
    version = args.tag.lstrip("v")

    root = ET.fromstring(METAINFO.read_text())  # noqa: S314
    releases = root.find("releases")
    entries = releases.findall("release") if releases is not None else []
    versions = [e.get("version") for e in entries]
    if version not in versions:
        print(f"no <release> for {version} in {METAINFO}", file=sys.stderr)
        return 1

    current = entries[versions.index(version)]
    lines = _render(current.find("description"))
    if not lines:
        lines = [f"grawji {version}."]

    print("\n".join(lines).strip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
