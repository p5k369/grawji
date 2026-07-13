"""Guard that every place pinning rawji names the same commit.

rawji is not on PyPI and never bumps its version, so grawji pins an
exact upstream commit.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Every file that pins the rawji commit.
PINNING_FILES = [
    ROOT / "pyproject.toml",
    ROOT / "flatpak" / "io.github.p5k369.grawji.yaml",
    ROOT / ".github" / "workflows" / "test.yml",
    ROOT / ".github" / "workflows" / "gui_smoke.yml",
]

_PIN = re.compile(
    r"(?:rawji@|rawji\.git.*\n\s*commit:\s*|pinpox/rawji@)([0-9a-f]{7,40})"
)


def _pin_of(path: Path) -> str:
    """Extract the rawji commit hash pinned in path."""
    match = _PIN.search(path.read_text())
    assert match is not None, f"{path.name} pins no rawji commit"
    return match.group(1)


def test_rawji_pins_agree():
    """All rawji pins name the same commit; bump them together."""
    pins = {path.name: _pin_of(path) for path in PINNING_FILES}
    assert len(set(pins.values())) == 1, f"rawji pins diverge: {pins}"


def test_rawji_pin_is_a_full_hash():
    """Pins are full 40-char hashes (short ones can go ambiguous)."""
    for path in PINNING_FILES:
        assert len(_pin_of(path)) == 40, f"{path.name} pins a short hash"
