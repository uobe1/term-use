#!/usr/bin/env python3
"""Unit tests for the output/replay reconstruction helpers — pure asserts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import replay
from termuse.errors import TDError


def test_linear_collapses_carriage_returns_to_the_final_state():
    """A progress bar redraws over one line; the reader must see the last
    frame, not every frame concatenated."""
    raw = " 0%\r 50%\r100%\ndone\n"
    assert replay.linear(raw) == "100%\ndone"


def test_linear_strips_escapes_but_keeps_layout():
    raw = "\x1b[31mRED\x1b[0m \x1b]0;title\x07TAIL\x1b[?25l\n"
    assert replay.linear(raw) == "RED TAIL"


def test_linear_handles_backspace_and_tabs():
    # backspace moves the cursor, it does not delete: X overwrites the 'b'
    assert replay.linear("abc\x08\x08X\n") == "aXc"
    assert replay.linear("a\tb\n") == "a       b"


def test_linear_blank_stream_is_empty_not_none():
    assert replay.linear("") == ""
    assert replay.linear("\n\n\n") == ""


def test_trim_keeps_head_and_tail_with_an_explicit_marker():
    text = "\n".join(f"line {i} pad" for i in range(2000))
    shown, omitted = replay.trim(text, 2000)
    assert omitted > 0
    assert len(shown.encode("utf-8")) < 2400, len(shown)
    assert "line 0 pad" in shown and "line 1999 pad" in shown
    assert "omitted" in shown, "truncation must be visible in the text itself"
    assert 'omitted' not in replay.trim("short", 2000)[0]
    assert replay.trim(text, 0) == (text, 0), "--max-bytes 0 means everything"


def test_trim_does_not_split_a_multibyte_character_into_an_error():
    text = "你好" * 2000
    shown, omitted = replay.trim(text, 100)
    assert omitted > 0
    assert isinstance(shown, str)  # decoded with replacement, never raised


def test_redrawn_detection_names_tui_style_output():
    assert replay.looks_redrawn("\x1b[?1049h\x1b[H hello") is True
    assert replay.looks_redrawn("just some lines\nof output\n") is False
    # Absolute positioning and cursor-up are redraw.
    assert replay.looks_redrawn("\x1b[12;1HCLICKED:3,4\x1b[K") is True
    assert replay.looks_redrawn("downloading \x1b[1Aoverwritten line") is True
    # A bare `2J` (+ `H`) is NOT enough: that is byte-for-byte what `clear` emits
    # in a plain shell, and a false positive makes the reader distrust a correct
    # read and re-verify a non-idempotent program. A program that really repaints
    # still matches via alt-screen, absolute positioning or cursor-up; missing the
    # rare clear-only painter is the cheap
    # error, distrusting a correct read is not.
    assert replay.looks_redrawn("\x1b[2J\x1b[HMENU_READY") is False
    assert replay.looks_redrawn("\x1b[H\x1b[2J\x1b[3J") is False   # `clear`
    # What used to fire on it, and misled both arms of the ab_write A/B down a
    # `replay` detour: colored traceback text (SGR bold) and bash's own bracketed
    # paste markers / prompt repaint are not a program repositioning its output.
    assert replay.looks_redrawn(
        '\x1b[?2004hTraceback:\r\n  File \x1b[35m"x.py"\x1b[0m, '
        'line \x1b[32m3\x1b[0m\r\n\x1b[?2004l') is False
    assert replay.looks_redrawn("\x1b[1;35mValueError\x1b[0m: bad") is False
    assert replay.looks_redrawn("\x1b[H\x1b[Jroot@host:~$ ") is False



def test_at_parsing_rejects_nonsense_with_a_useful_hint():
    for bad in ("offset:abc", "yesterday"):
        try:
            replay.resolve_at("no-such-session", bad)
            assert False, f"{bad} should not parse"
        except TDError as e:
            # session-missing may come first for a nonexistent session; the
            # shape errors must still be structured either way
            assert e.code in ("invalid-arguments", "session-missing"), e.to_json()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("test_replay: all passed")
