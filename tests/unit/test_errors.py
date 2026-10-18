#!/usr/bin/env python3
"""Unit tests for termuse.errors — pure asserts, no pytest."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse.errors import TDError, ERROR_CODES


def test_to_json_shape():
    e = TDError("timeout", "wait timed out after 3s",
                hint="increase --timeout", evidence={"screen": "abc"})
    obj = json.loads(e.to_json())
    assert "error" in obj, f"missing top-level 'error': {obj}"
    err = obj["error"]
    assert err["code"] == "timeout"
    assert err["message"] == "wait timed out after 3s"
    assert err["hint"] == "increase --timeout"
    assert err["evidence"] == {"screen": "abc"}


def test_optional_fields_default_null():
    e = TDError("session-missing", "no such session")
    err = json.loads(e.to_json())["error"]
    assert err["hint"] is None
    assert err["evidence"] is None


def test_error_code_table_is_the_whole_table():
    # Both directions. Checking only `expected - ERROR_CODES` let three codes
    # (`tmux-failed`, `shell-protocol-unavailable`, `pillow-missing`) be deleted
    # from the table with the suite green — and after the deletion,
    # `TDError.__init__`'s assert raises an AssertionError that escapes `main()`'
    # `except TDError`, so the structured failure becomes a traceback.
    expected = {
        "session-missing", "invalid-arguments", "socket-unreachable",
        "tmux-failed", "pane-dead", "pane-dead-by-signal",
        "no-shell-integration", "shell-not-ready", "unmanaged-pane",
        "shell-protocol-unavailable", "expect-code-mismatch", "timeout",
        "mouse-not-enabled", "tty-echo-broken", "font-missing",
        "pillow-missing",
    }
    assert set(ERROR_CODES) == expected, (
        f"missing: {expected - set(ERROR_CODES)}, "
        f"unexpected: {set(ERROR_CODES) - expected}")


def test_every_code_lives_on_a_path():
    """A code nobody raises is a lie in the table: the docs and the skill promise
    an obligation per code, and an agent cannot hit one that no path emits."""
    root = Path(__file__).resolve().parents[2] / "src"
    body = "".join(p.read_text(encoding="utf-8") for p in root.rglob("*.py")
                   if p.name != "errors.py")
    unused = [code for code in ERROR_CODES if f'"{code}"' not in body]
    assert not unused, f"codes never referenced outside errors.py: {unused}"


def test_codes_are_unique_and_kebab_case():
    assert len(ERROR_CODES) == len(set(ERROR_CODES)), "duplicate code in the table"
    kebab = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
    assert all(kebab.match(c) for c in ERROR_CODES), \
        f"codes must be kebab-case: {[c for c in ERROR_CODES if not kebab.match(c)]}"


def test_exit_code_nonzero():
    assert TDError("timeout", "x").exit_code != 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all tests passed")
