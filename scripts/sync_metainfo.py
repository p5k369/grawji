#!/usr/bin/env python3
"""Sync the AppStream metainfo release entry from the changelog."""

from __future__ import annotations

import datetime
import re
import sys
import tomllib
from pathlib import Path
from xml.sax.saxutils import escape

CHANGELOG = Path("CHANGELOG.md")
METAINFO = Path("data/io.github.p5k369.grawji.metainfo.xml")
PYPROJECT = Path("pyproject.toml")

_INDENT = "    "


def changelog_bullets(text: str, version: str) -> list[str]:
    """Return the cleaned bullet lines for a version's section.

    Markdown links and the trailing PR/commit references that
    release-please appends are stripped, so only the plain commit
    summary remains.
    """
    heading = re.compile(
        rf"^## \[?{re.escape(version)}\]?[^\n]*$", re.MULTILINE
    )
    match = heading.search(text)
    if match is None:
        return []
    section = text[match.end() :]
    next_heading = re.search(r"^## ", section, re.MULTILINE)
    if next_heading is not None:
        section = section[: next_heading.start()]

    bullets = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line.startswith("* "):
            continue
        item = line[2:]
        item = re.sub(r"\s*\(\[[^\]]*\]\([^)]*\)\)", "", item)
        item = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", item)
        item = item.replace("**", "")
        item = " ".join(item.split())
        if item:
            bullets.append(item)
    return bullets


def release_entry(
    version: str, date: str, bullets: list[str], intro: str | None = None
) -> str:
    """Render one metainfo release element for the version."""
    lines = [f'{_INDENT}<release version="{version}" date="{date}">']
    lines.append(f"{_INDENT}  <description>")
    if intro:
        lines.append(f"{_INDENT}    <p>{escape(intro)}</p>")
    lines.append(f"{_INDENT}    <ul>")
    lines.extend(
        f"{_INDENT}      <li>{escape(bullet)}</li>" for bullet in bullets
    )
    lines.append(f"{_INDENT}    </ul>")
    lines.append(f"{_INDENT}  </description>")
    lines.append(f"{_INDENT}</release>")
    return "\n".join(lines)


def upsert_release(metainfo: str, version: str, entry: str) -> str:
    """Insert the entry into the metainfo, or replace it if present."""
    existing = re.compile(
        rf'{_INDENT}<release version="{re.escape(version)}".*?</release>'
        rf"|{_INDENT}<release version=\"{re.escape(version)}\"[^/]*/>",
        re.DOTALL,
    )
    if existing.search(metainfo):
        return existing.sub(lambda _: entry, metainfo, count=1)
    return metainfo.replace("  <releases>", f"  <releases>\n{entry}", 1)


def main() -> int:
    """Sync the entry and report what happened."""
    pyproject = tomllib.loads(PYPROJECT.read_text())
    version = pyproject["project"]["version"]
    bullets = changelog_bullets(CHANGELOG.read_text(), version)
    if not bullets:
        print(f"no changelog section for {version}, nothing to sync")
        return 1
    date = datetime.datetime.now(tz=datetime.UTC).date().isoformat()
    entry = release_entry(version, date, bullets)
    METAINFO.write_text(upsert_release(METAINFO.read_text(), version, entry))
    print(f"synced metainfo release entry for {version} ({date})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
