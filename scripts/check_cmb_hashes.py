#!/usr/bin/env python3
"""Keep the Cambalache project's .ui hashes in sync with the .ui files."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

UI_DIR = Path("src/grawji/ui")
CMB = UI_DIR / "grawji.cmb"
_ENTRY = re.compile(r'<ui filename="([^"]+)" sha256="([0-9a-f]{64})"/>')


def main() -> int:
    """Rewrite stale hashes in grawji.cmb."""
    text = CMB.read_text(encoding="utf-8")
    changed: list[str] = []
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        filename, stored = match.group(1), match.group(2)
        path = UI_DIR / filename
        if not path.exists():
            missing.append(filename)
            return match.group(0)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != stored:
            changed.append(filename)
            return f'<ui filename="{filename}" sha256="{actual}"/>'
        return match.group(0)

    new_text = _ENTRY.sub(replace, text)

    for filename in missing:
        print(f"grawji.cmb references missing UI file: {filename}")
    if changed:
        CMB.write_text(new_text, encoding="utf-8")
        for filename in changed:
            print(f"updated grawji.cmb hash for {filename}")
        print("grawji.cmb was out of date. re-stage it and commit again.")
    if changed or missing:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
