#!/usr/bin/env python3
"""Unit tests for the completion sentinel — the shell that cannot report for itself.

The device case: an rc file hands bash to a prompt wrapper (blerust and friends)
that clears PS1/PS0/PROMPT_COMMAND and reads lines in its own program. No
injection can survive that, so term-use writes the marker itself — appended to the
line it types. That makes the *typed text* longer than the caller wrote, which is
only acceptable if the decision is exact, so every branch is pinned here: when it
fires, when it must not, and what the bytes it emits actually mean to our scanner.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import osc133, records, waiting

PROMPT = "\x1b]133;A\x07"
CMDLINE = "\x1b]133;B\x07"
C = "\x1b]133;C\x07"
D0 = "\x1b]133;D;0\x07"


def build(name, payloads, cmd="bash"):
    """A recording + state.json shaped like a live session; returns the temp root."""
    root = tempfile.mkdtemp(prefix="tu-sentinel-")
    os.environ["XDG_STATE_HOME"] = root
    path = records.session_dir(name) / "raw.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"version": 2, "width": 80, "height": 24, "timestamp": 1.0})]
    lines += [json.dumps([i * 0.1, "o", p]) for i, p in enumerate(payloads)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (path.parent / "state.json").write_text(json.dumps({
        "session": name, "socket": None, "cmd": cmd,
        "shell_integration": False, "last_send_offset": len(lines[0]) + 1}),
        encoding="utf-8")
    return root


def plan(name, text="ls -la", **kw):
    return waiting.marker_plan(name, records.read_state(name), text, **kw)


def test_a_shell_with_no_prompt_cycle_gets_our_sentinel():
    # Nothing but the banner: no A/B (prompt machinery) and no D (completion).
    root = build("s1", ["Welcome\n", "$ \x1b[?2004h"])
    try:
        assert plan("s1") == ("sentinel", None)
        assert records.shell_markers_proven("s1") is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_shell_that_reports_for_itself_is_left_alone():
    root = build("s2", [PROMPT, CMDLINE, C, "out\r\n", D0])
    try:
        assert records.shell_markers_proven("s2") is True
        assert plan("s2") == ("shell", None)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_our_own_sentinel_never_certifies_a_dead_shell():
    """The trap this rule exists to avoid: the sentinel writes C and D, so counting
    any `D` as proof would report the shell as integrated after the first line we
    had to narrate ourselves — and the next unsafe line would silently get no
    marker at all."""
    root = build("s3", [C, D0])          # exactly what a sentinel emits
    try:
        assert records.shell_markers_proven("s3") is False
        assert plan("s3", "ls -la") == ("sentinel", None)
        assert plan("s3", "sleep 1 &")[0] == "skipped"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_sentinel_bytes_are_a_real_completion_to_the_scanner():
    """The whole bet is that these bytes read as preexec-then-done with the code."""
    typed = 'false' + waiting.SENTINEL
    emitted = "\x1b]133;C\x07\x1b]133;D;1\x07"
    assert "\\033]133;C\\007" in waiting.SENTINEL, waiting.SENTINEL
    assert typed.startswith("false; __tu_ec=$?;")
    sc = osc133.OSC133Scanner()
    kinds = [(e["kind"], e.get("code")) for e in sc.feed(emitted)]
    assert kinds == [("preexec", None), ("done", 1)], kinds


def test_unsafe_lines_are_refused_with_a_reason():
    unsafe = {
        "sleep 1 &": "background",
        "echo a |": "operator",
        "if x; then": "open block",
        "cat <<EOF": "heredoc",
        "echo hi # note": "comment",
        'echo "unclosed': "unbalanced",
        "ls \\": "continues",
        "": "nothing",
    }
    for text, needle in unsafe.items():
        reason = waiting.sentinel_refusal(text)
        assert reason and needle in reason, (text, reason)
    for text in ("ls -la", "make && make install", "for i in 1 2; do echo $i; done",
                 "git commit -m 'msg'", "a | b | c", "docker ps -q | xargs rm"):
        assert waiting.sentinel_refusal(text) is None, text


def test_only_shells_that_speak_dollar_question_are_offered():
    for cmd, ok in (("bash", True), ("sh", True), ("dash", True), ("zsh", True),
                    ("env ZDOTDIR=/x zsh", True), ("/bin/ksh", True),
                    ("fish", False), ("csh", False), ("tcsh", False),
                    ("python3", False), (None, False)):
        assert waiting.sentinel_possible({"cmd": cmd}) is ok, cmd


def test_a_line_ending_in_a_semicolon_does_not_get_a_double_one():
    """`cd /tmp;` + `; __tu_ec=…` is `cd /tmp;;` — a bash syntax error, which is
    worse than the missing marker. The suffix drops its own separator instead."""
    assert waiting.sentinel_for("ls").startswith("; __tu_ec=")
    assert waiting.sentinel_for("cd /tmp; ").startswith(" __tu_ec=")
    joined = "cd /tmp;" + waiting.sentinel_for("cd /tmp;")
    assert ";;" not in joined, joined
    assert waiting.sentinel_refusal("cd /tmp;") is None, "a complete line stays markable"


def test_the_switch_disables_it_without_lying_about_the_shell():
    root = build("s4", ["Welcome\n"])
    try:
        mode, reason = plan("s4", enabled=False)
        assert mode == "skipped" and "TERM_USE_SENTINEL" in reason, (mode, reason)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_unmanaged_and_empty_sends_are_not_tracked():
    assert waiting.marker_plan("no-such-session", None, "ls") == (
        "skipped", "no records cover this pane, so nothing is tracked")
    root = build("s5", ["Welcome\n"])
    try:
        assert plan("s5", "") == ("skipped", "nothing was typed")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("test_sentinel: all passed")
