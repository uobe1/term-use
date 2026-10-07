#!/usr/bin/env python3
"""Unit tests for the ShellReady condition (wait --shell-ready)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import waiting
from termuse.errors import TDError
from termuse.waiting import ShellReady, UntilRegex, WaitContext


def ctx_with(screen: str) -> WaitContext:
    return WaitContext(capture=lambda sb: screen,
                       meta=lambda: {"cursor_x": 0, "cursor_y": 0})


def test_label_confidence_and_json_shape():
    sr = ShellReady("td-unit-any")
    assert sr.label == "shell-ready"
    assert sr.confidence == "fact"
    j = sr.to_json(False)
    assert j["type"] == "shell-ready" and j["met"] is False
    assert j["expect_code"] == 0, "the probe always expects exit 0"


def test_evaluate_delegates_to_cmd_done():
    sr = ShellReady("td-unit-any")

    class FakeDone:
        exit_code = 0

        def evaluate(self, ctx):
            return True

        def to_json(self, met):
            return {"type": "cmd-done", "confidence": "fact", "met": met,
                    "expect_code": 0, "exit_code": 0}

    sr._done = FakeDone()
    assert sr.evaluate(ctx_with("$ "))
    j = sr.to_json(True)
    assert j["type"] == "shell-ready" and j["exit_code"] == 0


def test_evaluate_without_state_raises_structured():
    sr = ShellReady("td-unit-definitely-missing-state")
    try:
        sr.evaluate(ctx_with("$ "))
    except TDError as e:
        assert e.code == "session-missing"
        return
    raise AssertionError("expected TDError for missing state.json")


def test_allof_takes_weakest_with_until():
    sr = ShellReady("td-unit-any")
    combo = waiting.AllOf([sr, UntilRegex("$ ")])
    assert combo.confidence == "inference", "weakest member wins"


class _FakeDone:
    """CmdDone stand-in: evaluate verdict fixed, exit_code recorded on success
    (mirrors the real CmdDone contract the timeout diagnosis relies on)."""

    def __init__(self, answered: bool):
        self.answered = answered
        self.exit_code = 0 if answered else None

    def evaluate(self, ctx):
        return self.answered

    def to_json(self, met):
        return {"type": "cmd-done", "confidence": "fact", "met": met,
                "expect_code": 0, "exit_code": self.exit_code if met else None}


def test_timeout_diagnosis_probe_unanswered_is_shell_not_ready():
    sr = ShellReady("td-unit-any")
    sr._done = _FakeDone(answered=False)
    combo = waiting.AllOf([sr, UntilRegex("ZZZ_NEVER")])
    assert waiting.shell_ready_unmet(ctx_with("$ "), combo), \
        "unanswered probe -> shell-not-ready"


def test_timeout_diagnosis_probe_answered_is_plain_timeout():
    sr = ShellReady("td-unit-any")
    sr._done = _FakeDone(answered=True)
    combo = waiting.AllOf([sr, UntilRegex("ZZZ_NEVER")])
    assert not waiting.shell_ready_unmet(ctx_with("$ "), combo), \
        "answered probe -> the laggard is --until, plain timeout"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all tests passed")
