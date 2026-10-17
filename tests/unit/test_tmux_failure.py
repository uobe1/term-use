#!/usr/bin/env python3
"""Unit tests for tmux success/failure detection — pure asserts, no pytest.

Why this exists: tmux can print an error on stderr and still exit 0 (measured
with tmux 3.7c against a default socket owned by another security context),
and term-use used to read that as "session already exists" / "screen is empty".
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import tmuxio
from termuse.errors import TDError


def res(rc, stdout="", stderr=""):
    return subprocess.CompletedProcess(["tmux"], rc, stdout, stderr)


def test_ok_requires_clean_stderr_too():
    assert tmuxio.ok(res(0, "out", "")) is True
    assert tmuxio.ok(res(0, "", "access not allowed")) is False
    assert tmuxio.ok(res(1, "", "no server running")) is False
    assert tmuxio.ok(res(0, "out", "  \n")) is True  # whitespace is not an error


def test_classifies_socket_access_failure():
    code, hint = tmuxio.classify(res(0, "", "access not allowed"))
    assert code == "socket-unreachable", code
    assert "--socket" in hint and "TMUX_TMPDIR" in hint, hint


def test_classifies_missing_session():
    for msg in ("no server running on /tmp/tmux-0/default",
                "can't find session: gone",
                "no sessions"):
        code, _hint = tmuxio.classify(res(1, "", msg))
        assert code == "session-missing", (msg, code)


def test_unknown_failure_keeps_callers_code():
    code, hint = tmuxio.classify(res(1, "", "some brand new tmux complaint"))
    assert code == "tmux-failed" and hint is None


def test_require_raises_structured_error_not_exception():
    try:
        tmuxio.require(res(0, "", "access not allowed"))
        assert False, "must raise"
    except TDError as e:
        assert e.code == "socket-unreachable", e.to_json()
        assert "access not allowed" in e.message
        assert e.hint, "an unreachable socket needs an actionable hint"


def test_require_keeps_caller_code_when_unclassified():
    try:
        tmuxio.require(res(1, "", "weird complaint"), "pane-dead")
        assert False, "must raise"
    except TDError as e:
        assert e.code == "pane-dead", e.to_json()


def test_require_returns_stdout_on_success():
    assert tmuxio.require(res(0, "%7\n", "")) == "%7\n"


def test_signal_name_normalizes_across_tmux_builds():
    # Measured: "9" on tmux 3.7c/Linux, "KILL" on the Termux device.
    assert tmuxio.signal_name("9") == "KILL"
    assert tmuxio.signal_name("KILL") == "KILL"
    assert tmuxio.signal_name("TERM") == "TERM"
    assert tmuxio.signal_name("99999") == "99999"  # unknown number: pass through


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("test_tmux_failure: all passed")
