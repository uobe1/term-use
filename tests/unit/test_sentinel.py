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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import osc133, records, waiting

PROMPT = "\x1b]133;A\x07"
CMDLINE = "\x1b]133;B\x07"
C = "\x1b]133;C\x07"
D0 = "\x1b]133;D;0\x07"


def build(name, payloads, cmd="bash", integration=False):
    """A recording + state.json shaped like a live session; returns the temp root.

    A payload is either a bare string (pane output) or an ("i", records) tuple
    for a client injection, so a fixture can carry the send itself and not just
    what the pane said back.
    """
    root = tempfile.mkdtemp(prefix="tu-sentinel-")
    os.environ["XDG_STATE_HOME"] = root
    path = records.session_dir(name) / "raw.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"version": 2, "width": 80, "height": 24, "timestamp": 1.0})]
    for i, payload in enumerate(payloads):
        code, data = payload if isinstance(payload, tuple) else ("o", payload)
        lines.append(json.dumps([i * 0.1, code, data]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (path.parent / "state.json").write_text(json.dumps({
        "session": name, "socket": None, "cmd": cmd,
        "shell_integration": integration,
        "last_send_offset": len(lines[0]) + 1}),
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


def test_an_injected_shell_with_a_prompt_is_not_narrated_before_its_first_done():
    """`shell_markers_proven` needs a D as well as A/B; an injected shell that
    has only painted its first prompt is already integrated, and lengthening
    the first line there shifts the program's screen."""
    root = build("s2b", [PROMPT, CMDLINE], cmd="bash", integration=True)
    try:
        assert records.shell_markers_proven("s2b") is False
        assert plan("s2b", "echo hi") == ("shell", None)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_non_sentinel_shell_with_its_own_markers_is_left_alone():
    """fish >= 4 cannot carry our suffix, but its stream carries A/B before the
    first command completes. That is its own channel, not "no marker"."""
    root = build("s8", [PROMPT, CMDLINE], cmd="fish")
    try:
        assert records.shell_markers_proven("s8") is False  # no D yet
        assert plan("s8") == ("shell", None)
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
        "cat <<EOF": "heredoc",
        "echo hi # note": "comment",
        "ls \\": "continues",
        "": "nothing",
    }
    for text, needle in unsafe.items():
        reason = waiting.sentinel_refusal(text)
        assert reason and needle in reason, (text, reason)
    for text in ("ls -la", "make && make install", "for i in 1 2; do echo $i; done",
                 "git commit -m 'msg'", "a | b | c", "docker ps -q | xargs rm"):
        assert waiting.sentinel_refusal(text) is None, text


def test_complete_lines_with_quotes_and_keywords_are_not_refused():
    """Completeness is the shell's question, not a character count. Counting
    quotes and `then`/`done` tokens refused ordinary commands (`echo done`,
    `echo "don't"`) before the parser was ever consulted."""
    for text in ('echo "don\'t panic"',
                 'git commit -m "fix: it\'s done"',
                 'echo "a & b"',
                 "echo done", "echo then",
                 "case x in a) echo hi;; esac",
                 "echo $((1 + 2))"):
        assert waiting.sentinel_refusal(text) is None, text


def test_the_parser_still_refuses_an_unclosed_quote():
    root = build("s12", ["Welcome\n"])
    try:
        mode, reason = plan("s12", 'echo "unclosed')
        assert mode == "skipped" and "parser" in reason, (mode, reason)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_program_pane_is_never_narrated():
    """A shell invoked with -c or a script operand runs a program; the next
    line is that program's stdin, so appending shell syntax would corrupt it
    (e.g. `sh -c python3` executes the suffix). Interactive shells are still
    narrated, and a program whose stream carries markers is still believed."""
    for n, cmd in enumerate(("bash -c 'python3'", "bash script.sh",
                             "sh -c python3", "bash -c 'cat'")):
        name = f"prog{n}"
        root = build(name, ["Welcome\n"], cmd=cmd)
        try:
            mode, reason = plan(name, "echo hello")
            assert mode == "skipped", (cmd, mode, reason)
            assert "program" in reason or "script" in reason, (cmd, reason)
        finally:
            shutil.rmtree(root, ignore_errors=True)
    root = build("progmarkers", [PROMPT, CMDLINE], cmd="bash -c 'fish'")
    try:
        assert plan("progmarkers") == ("shell", None), \
            "a program whose stream carries markers still reports them"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_shells_own_parser_is_the_general_completeness_check():
    """The shape list can never name every construct; the shell can. The parser
    is asked about the exact line we would type, suffix included."""
    incomplete = ("while true", "if true", "case x in", "function f()",
                  "for x in a b", "select x in a b")
    for line in incomplete:
        reason = waiting.parser_refusal(line + waiting.sentinel_for(line), "bash")
        assert reason, line
    complete = ("true", "echo hi", "if true; then echo x; fi", "cd /tmp;",
                "make && make install", "for i in 1 2; do echo $i; done")
    for line in complete:
        assert waiting.parser_refusal(
            line + waiting.sentinel_for(line), "bash") is None, line
    # `!` is syntactically a complete line to bash, so the shape guard refuses
    # it before the parser: the suffix would read the reserved word's status.
    assert waiting.sentinel_refusal("!")


def test_parser_check_never_executes_the_candidate():
    """`-n` reads, it does not run: a substitution in the caller's line must not
    be executed by the completeness check."""
    marker = Path(tempfile.mkdtemp(prefix="tu-parser-")) / "ran"
    try:
        line = f"echo $(touch {marker})"
        assert waiting.parser_refusal(
            line + waiting.sentinel_for(line), "bash") is None
        assert not marker.exists(), "the parser executed a command substitution"
    finally:
        shutil.rmtree(marker.parent, ignore_errors=True)


def test_a_shell_with_no_parser_check_is_refused_not_guessed():
    assert waiting.parser_refusal("true" + waiting.SENTINEL, "csh")


def test_marker_plan_uses_the_parser_for_open_constructs():
    root = build("s7", ["Welcome\n"])
    try:
        mode, reason = plan("s7", "while true")
        assert mode == "skipped" and "parser" in reason, (mode, reason)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_only_shells_that_speak_dollar_question_are_offered():
    for cmd, ok in (("bash", True), ("sh", True), ("dash", True), ("zsh", True),
                    ("env ZDOTDIR=/x zsh", True), ("env -i zsh", True),
                    ("/bin/ksh", True), ("nice -n 5 bash", True),
                    ("stdbuf -o 0 bash", True), ("env -u FOO bash", True),
                    ("fish", False), ("csh", False), ("tcsh", False),
                    ("python3", False), (None, False)):
        assert waiting.sentinel_possible({"cmd": cmd}) is ok, cmd


def test_wrapper_arguments_do_not_become_the_shell():
    assert waiting.shell_word("nice -n 5 bash") == "bash"
    assert waiting.shell_word("nice --adjustment 5 bash") == "bash"
    assert waiting.shell_word("stdbuf -o 0 bash") == "bash"
    assert waiting.shell_word("stdbuf -o0 bash") == "bash"
    assert waiting.shell_word("env -u FOO bash") == "bash"
    assert waiting.shell_word("env FOO=bar bash") == "bash"
    assert waiting.shell_word("env -i zsh") == "zsh"


def test_a_negative_marker_probe_is_cached_briefly():
    """A program pane pays the marker probe on every send; a negative result
    must not add 1.5 s per line, and it must expire so a late remote shell is
    discovered again."""
    root = build("probe", ["Welcome\n"], cmd="python3")
    try:
        st = records.read_state("probe")
        mode, _ = waiting.marker_plan("probe", st, "x", probe_wait_s=0.05)
        assert mode == "skipped"
        assert st.get("marker_probe_until", 0) > time.time(), st
        assert waiting.marker_probe_wait(st) == 0.0
        assert waiting.marker_probe_wait({"marker_probe_until": time.time() - 1}) == 1.5
        assert waiting.marker_probe_wait(None) == 1.5
        assert waiting.marker_probe_wait({}) == 1.5
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_line_ending_in_a_semicolon_does_not_get_a_double_one():
    """`cd /tmp;` + `; __tu_ec=…` is `cd /tmp;;` — a bash syntax error, which is
    worse than the missing marker. The suffix drops its own separator instead."""
    assert waiting.sentinel_for("ls").startswith("; __tu_ec=")
    assert waiting.sentinel_for("cd /tmp; ").startswith(" __tu_ec=")
    joined = "cd /tmp;" + waiting.sentinel_for("cd /tmp;")
    assert ";;" not in joined, joined
    assert waiting.sentinel_refusal("cd /tmp;") is None, "a complete line stays markable"


def test_the_compound_note_names_the_callers_stage_not_our_suffix():
    """`line_scope` reads the recorded injection, which now includes our own
    `; __tu_ec=$?; printf …`. Quoting that back as "the command the exit code
    came from" blames the caller's `;` for text term-use appended."""
    line = "deploy.py init; " + waiting.sentinel_for("deploy.py init; ")
    assert waiting.strip_sentinel(line) == "deploy.py init; "
    note = waiting.line_scope(waiting.strip_sentinel(line))
    assert note and "deploy.py init" in note and "printf" not in note, note
    two = "make && make install" + waiting.SENTINEL
    note2 = waiting.line_scope(waiting.strip_sentinel(two))
    assert note2 and "make install" in note2 and "printf" not in note2, note2
    assert waiting.strip_sentinel("ls -la") == "ls -la"


def _rewrite_state(name, state):
    path = records.session_dir(name) / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")


def test_a_tracked_line_that_never_answered_pauses_the_narration():
    """The guard is our own outstanding marker, not a process name: no marker
    back means the shell never ran that line, so the next line is the program's
    input and appending shell syntax to it is corruption."""
    root = build("s6", [("i", [{"text": "cat"}, {"key": "Enter"}]),
                       ("o", C), ("o", D0)])
    try:
        size = (records.session_dir("s6") / "raw.log").stat().st_size
        st = records.read_state("s6")
        st["marker_source"] = "sentinel"
        st["last_tracked_offset"] = 0
        _rewrite_state("s6", st)
        assert waiting.tracked_line_answered("s6", st) is True, "marker did arrive"
        st["last_tracked_offset"] = size
        _rewrite_state("s6", st)
        assert waiting.tracked_line_answered("s6", st) is False, "nothing since"
        # A paused send is not a new tracked line: it must not overwrite the
        # outstanding proof, or the next line would get a sentinel appended
        # into the program that still holds the tty.
        for overwrite in ("shell", "paused", "none", "skipped"):
            st["marker_source"] = overwrite
            _rewrite_state("s6", st)
            assert waiting.tracked_line_answered("s6", st) is False, overwrite
        st["marker_source"] = "paused"
        st["last_tracked_offset"] = 0
        _rewrite_state("s6", st)
        assert waiting.tracked_line_answered("s6", st) is True, \
            "the recorded D answers the outstanding tracked line"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_an_unrecognized_shell_names_itself_in_the_skip_reason():
    root = build("s9", ["Welcome\n"], cmd="myshell --rcfile /tmp/rc -i")
    try:
        mode, reason = plan("s9", "true")
        assert mode == "skipped" and "myshell" in reason, (mode, reason)
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
