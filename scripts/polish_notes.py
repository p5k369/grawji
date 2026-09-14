#!/usr/bin/env python3
"""Rewrite the metainfo release entry into user-facing prose via AI model."""

from __future__ import annotations

import datetime
import os
import sys
import tomllib

import anthropic
from sync_metainfo import (
    CHANGELOG,
    METAINFO,
    PYPROJECT,
    changelog_bullets,
    release_entry,
    upsert_release,
)

MODEL = "claude-opus-5"

_SYSTEM = """\
You write end-user release notes for grawji, a GTK4 desktop app that
develops Fujifilm RAF photos through the camera's own conversion
engine over USB.

The input is the developer changelog of one release. Answer with plain
text only, in exactly this shape: the first line is one short intro
sentence for the release, every following line is one user-visible
change starting with "- ". Use user-facing language, drop commit
scopes, developer jargon, and purely internal changes, merge related
items, and use American English. At most 10 bullets, no markdown, no
empty lines.

Describe ONLY changes that appear in the input. Never invent, infer or
pad with plausible-sounding items: a release with a single input change
gets exactly one bullet."""


def parse_notes(text: str, source_count: int) -> tuple[str, list[str]]:
    """Validate a model answer."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items = [line[2:].strip() for line in lines if line.startswith("- ")]
    if not lines or lines[0].startswith("- ") or not items:
        raise ValueError(f"unexpected response shape:\n{text}")
    if len(items) > source_count:
        raise ValueError(
            f"{len(items)} bullets from {source_count} changes,"
            f" refusing invented content:\n{text}"
        )
    return lines[0], items


def draft_notes(version: str, bullets: list[str]) -> tuple[str, list[str]]:
    """Ask Model for an intro line and user-facing bullets."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Version {version}\n\nChangelog:\n"
                    + "\n".join(f"* {b}" for b in bullets)
                    + "\n\nRewrite this changelog into the release notes"
                    " now, exactly in the requested shape."
                ),
            }
        ],
    )
    text = next(
        (block.text for block in response.content if block.type == "text"),
        "",
    )
    return parse_notes(text, len(bullets))


def main() -> int:
    """Polish the entry, or leave the synced draft on any failure."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("no ANTHROPIC_API_KEY, keeping the plain synced entry")
        return 0
    pyproject = tomllib.loads(PYPROJECT.read_text())
    version = pyproject["project"]["version"]
    bullets = changelog_bullets(CHANGELOG.read_text(), version)
    if not bullets:
        print(f"no changelog section for {version}, nothing to polish")
        return 0
    for last_attempt in (False, True):
        try:
            intro, items = draft_notes(version, bullets)
            break
        except anthropic.APIError as exc:
            print(f"Model call failed, keeping the plain entry: {exc}")
            return 0
        except ValueError as exc:
            if last_attempt:
                print(f"unusable response, keeping the plain entry: {exc}")
                return 0
            print(f"unusable response, retrying once: {exc}")
    date = datetime.datetime.now(tz=datetime.UTC).date().isoformat()
    entry = release_entry(version, date, items, intro=intro)
    METAINFO.write_text(upsert_release(METAINFO.read_text(), version, entry))
    print(f"polished metainfo release entry for {version} via {MODEL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
