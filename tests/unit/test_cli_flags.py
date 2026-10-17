#!/usr/bin/env python3
"""Unit tests for new CLI flags: send --repeat/--delay, wait --shell-ready."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse.cli import build_parser
from termuse.errors import ERROR_CODES


def test_send_repeat_defaults():
    args = build_parser().parse_args(["send", "-n", "x", "--key", "Up"])
    assert args.repeat == 1
    assert args.delay == 0.35, "default gap clears real menu debouncing"


def test_send_repeat_parse():
    args = build_parser().parse_args(
        ["send", "-n", "x", "--key", "C-c", "--repeat", "2", "--delay", "0.3"])
    assert args.repeat == 2 and args.delay == 0.3


def test_wait_shell_ready_default_off():
    args = build_parser().parse_args(["wait", "-n", "x", "--cmd-done"])
    assert args.shell_ready is False


def test_wait_shell_ready_parse():
    args = build_parser().parse_args(
        ["wait", "-n", "x", "--shell-ready", "--timeout", "5"])
    assert args.shell_ready is True


def test_shell_not_ready_registered():
    assert "shell-not-ready" in ERROR_CODES


def test_version_flag_exists():
    # Agents probe `--version` like any CLI. Without it argparse answered
    # "the following arguments are required: COMMAND", which reads like a
    # malformed call rather than a supported question.
    import io
    import contextlib
    from termuse import __version__
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            build_parser().parse_args(["--version"])
    except SystemExit as exc:
        assert exc.code == 0, exc
    else:
        raise AssertionError("--version must exit 0 after printing")
    assert out.getvalue().strip() == f"term-use {__version__}", out.getvalue()


def test_version_matches_pyproject():
    # Two places state the version; they must not be allowed to drift.
    import re
    from pathlib import Path
    from termuse import __version__
    text = Path(__file__).resolve().parents[2].joinpath("pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert m, "no version in pyproject.toml"
    assert m.group(1) == __version__, f"{m.group(1)} != {__version__}"


def test_screenshot_has_no_format_choice():
    args = build_parser().parse_args(["screenshot", "-n", "x"])
    assert not hasattr(args, "format"), "--format was dropped with the jpg path"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all tests passed")
