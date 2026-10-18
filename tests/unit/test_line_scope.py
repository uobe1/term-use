#!/usr/bin/env python3
"""Unit tests for `line_scope` — what one exit code may claim about a line."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse.waiting import line_scope, unquoted


def test_single_command_has_nothing_to_caveat():
    assert line_scope("python3 deploy.py publish") is None
    assert line_scope("ls -la") is None
    assert line_scope(None) is None
    assert line_scope("") is None


def test_semicolons_inside_quotes_are_data_not_syntax():
    # The exact shape that would false-positive on a naive substring search:
    # a one-liner whose own code contains `;`. Flagging it would make a correct
    # `fact` look partial, which is worse than saying nothing.
    assert line_scope("python3 -c 'import time; time.sleep(1)'") is None
    assert line_scope('python3 -c "print(1); print(2)"') is None
    assert line_scope("echo 'a && b ; c'") is None
    assert line_scope("grep -E 'x|y' f") is None
    assert line_scope("sh -c 'run; other' && cat log") is not None  # the outer &&


def test_semicolon_line_names_the_command_the_code_came_from():
    note = line_scope("deploy.py init; deploy.py build")
    assert "`;`" in note and "failed unseen" in note, note
    assert "deploy.py build" in note, f"must name the covered stage: {note}"
    assert "deploy.py init" not in note, "the earlier stage is not the answer"


def test_short_circuit_operators_get_their_own_meaning():
    # `&&` and `||` invert each other on a zero, and one note covering both used
    # to tell `||` users that zero meant every stage passed — when it means the
    # last stage never ran.
    note = line_scope("make && make install")
    assert "`&&`" in note and "zero means every stage passed" in note, note
    assert "`||`" not in note, f"&& must not inherit the || caveat: {note}"
    assert "make install" in note, f"must name the last stage: {note}"
    note2 = line_scope("a || b")
    assert "`||`" in note2 and "`&&`" not in note2 and "`;`" not in note2, note2
    assert "already succeeded" in note2, note2
    both = line_scope("a && b || c")
    assert "`&&`" in both and "`||`" in both, both


def test_pipeline_reports_the_last_stage():
    note = line_scope("make build 2>&1 | tail -5")
    assert "pipeline" in note and "tail -5" in note, note
    # `2>&1` is a redirection, not a pipeline, and not a background job:
    assert line_scope("make build > build.log 2>&1") is None


def test_split_keeps_quoted_separators_inside_one_segment():
    from termuse.waiting import split_top_level
    assert split_top_level("python3 -c 'a; b'") == ["python3 -c 'a; b'"]
    assert split_top_level("a; b && c | d") == ["a", " b ", " c ", " d"]
    # `2>&1` must not split, and a trailing `&` leaves nothing behind it:
    assert len(split_top_level("cmd > out.log 2>&1")) == 1
    assert split_top_level("cmd &") == ["cmd "]
    assert "launched, not finished" in line_scope("cmd &")


def test_background_job_is_launched_not_finished():
    note = line_scope("long_task &")
    assert note and "launched, not finished" in note


def test_comment_after_hash_does_not_count():
    assert line_scope("ls  # list; everything") is None


def test_escaped_separator_does_not_split():
    assert line_scope("echo a\\;b") is None


def test_newline_separates_too():
    note = line_scope("cd /tmp\nls")
    assert "`;`" in note and "ls" in note, note


def test_unquoted_blanks_only_quoted_regions():
    assert unquoted("a 'b c' d") == "a" + " " * 7 + "d"
    # An escaped quote inside double quotes does not end the region:
    assert unquoted('a "b\\"c" ; d').count(";") == 1
    assert ";" not in unquoted("a 'b ; c' d")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
