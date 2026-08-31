"""Tests for CameraOpsController."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

import grawji.controllers.camera_ops as module
from grawji.controllers.camera_ops import CameraOpsController


class ImmediateThread:
    """threading.Thread stand-in that runs the target synchronously."""

    def __init__(self, *, target, name=None, daemon=None, args=()):
        """Record the target like threading.Thread would."""
        self._target = target
        self._args = args

    def start(self):
        """Run the target in place of spawning a thread."""
        self._target(*self._args)


@pytest.fixture
def immediate(monkeypatch):
    """Run worker threads and idle callbacks synchronously."""
    monkeypatch.setattr(module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(module.GLib, "idle_add", lambda fn, *a: fn(*a))


class FakeSession:
    """Records calls; behavior configured per test."""

    def __init__(self, **behavior):
        """Map method names to return values or exceptions."""
        self.behavior = behavior
        self.calls = []

    def __getattr__(self, name):
        """Record any call and serve the configured behavior."""

        def call(*args):
            self.calls.append((name, args))
            result = self.behavior.get(name)
            if isinstance(result, Exception):
                raise result
            return result

        return call


def result(slots, dropped=None, model="X-E5"):
    """A transfer-result stand-in."""
    return SimpleNamespace(model=model, slots=slots, dropped=dropped or {})


def test_load_bank_names_reports_names(immediate):
    """Names read from the body reach the callback."""
    session = FakeSession(read_bank_names=["BW", "PORTRA"])
    got = []
    CameraOpsController(session).load_bank_names(got.append)
    assert got == [["BW", "PORTRA"]]


def test_load_bank_names_swallows_errors(immediate):
    """A camera error degrades to an empty name list."""
    session = FakeSession(read_bank_names=RuntimeError("0x2019"))
    got = []
    CameraOpsController(session).load_bank_names(got.append)
    assert got == [[]]


def test_transfer_reports_banks_fs_and_dropped(immediate):
    """The done message names slots, dial positions and drops."""
    session = FakeSession(
        transfer_bank_recipes=result([0, 2], {"0": {"clarity"}}),
        transfer_fs_recipes=result([1]),
    )
    messages, errors = [], []
    CameraOpsController(session).run_bank_transfer(
        {0: object(), 2: object()},
        {},
        {1: object()},
        messages.append,
        errors.append,
    )
    assert errors == []
    (message,) = messages
    assert "C1, C3, FS2" in message
    assert "clarity" in message


def test_transfer_errors_are_reported_not_raised(immediate):
    """Transfer failures land in on_error, nothing escapes."""
    session = FakeSession(
        transfer_bank_recipes=RuntimeError("checksum 0x200f")
    )
    messages, errors = [], []
    CameraOpsController(session).run_bank_transfer(
        {0: object()}, {}, {}, messages.append, errors.append
    )
    assert messages == []
    assert "0x200f" in errors[0]
