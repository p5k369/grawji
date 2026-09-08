"""Tests for the command-line entry point."""

from __future__ import annotations

import pytest

import grawji
from grawji.__main__ import main


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_prints_version_and_exits(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The version flags print the version without starting the app."""
    assert main(["grawji", flag]) == 0
    assert capsys.readouterr().out == f"grawji {grawji.__version__}\n"
