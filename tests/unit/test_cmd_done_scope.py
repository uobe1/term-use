#!/usr/bin/env python3
"""Unit tests for `CmdDone`'s marker scoping — the race a loaded machine exposed.

`send` takes its offset from the recording's current size, but the recorder lags
the pane: when the shell is busy, the *previous* command's `C` and `D` can both
land after that offset. Position-checking the `D` alone then answers "finished,
exit 0" for a command that has not started. A real tmux run reproduced it only
under load, so the shape is pinned here instead.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse.errors import TDError
from termuse.waiting import CmdDone

C = "\x1b]133;C\x07"
D0 = "\x1b]133;D;0\x07"
A = "\x1b]133;A\x07"
B = "\x1b]133;B\x07"


class Ctx:
    """No tmux behind a fake session: the native channel must fall through."""

    def meta(self):
        raise TDError("session-missing", "no tmux in this test")

    def capture(self, scrollback=0):
        return ""


def build(name, payloads, offset_after=None):
    """Write raw.log with one o-event per payload; return the session name.

    `offset_after` = how many payloads belong to the *previous* send, so the
    offset lands right after them — the state a busy shell puts the recorder in.
    """
    root = tempfile.mkdtemp(prefix="tu-cmddone-")
    os.environ["XDG_STATE_HOME"] = root
    from termuse import records
    path = records.session_dir(name) / "raw.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"version": "2", "width": 80, "height": 24,
                         "timestamp": 1.0})]
    lines += [json.dumps([i * 0.1, "o", p]) for i, p in enumerate(payloads)]
    body = "\n".join(lines) + "\n"
    path.write_text(body, encoding="utf-8")
    # Byte offset of the line AFTER the first `offset_after` payloads.
    cut = len("\n".join(lines[:1 + offset_after])) + 1 if offset_after else len(lines[0]) + 1
    (path.parent / "state.json").write_text(json.dumps({
        "session": name, "socket": None, "last_send_offset": cut,
    }), encoding="utf-8")
    return root


def test_previous_commands_markers_cannot_answer_for_this_send():
    # The recorder wrote the previous C before our offset, and its D lands after.
    root = build("cd1", [C, "output", D0 + A + B], offset_after=2)
    try:
        cond = CmdDone("cd1")
        assert cond.evaluate(Ctx()) is False, \
            "a D whose C predates this send belongs to the command before it"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_this_commands_markers_do_answer():
    # Normal ordering: our line echoed, PS0 fired, then our own done.
    root = build("cd2", [C, "x", "$ echo hi\r\nhi\r\n", C, "hi\r\n", D0 + A],
                 offset_after=3)
    try:
        cond = CmdDone("cd2")
        assert cond.evaluate(Ctx()) is True, "our own C..D pair must still be fact"
        assert cond.exit_code == 0 and cond.via == "osc133", (cond.exit_code, cond.via)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_genuine_completion_wins_over_a_borrowed_one():
    # A stream holding both: the previous command's borrowed C..D before the
    # offset, our own after it with a different code. The answer must be 3 —
    # taking the borrowed pair would report the other command's success.
    root = build("cd3", [C, "old", "\x1b]133;D;0\x07" + A + B,
                         "$ make\r\n", C, "built\r\n\x1b]133;D;3\x07" + A],
                 offset_after=3)
    try:
        cond = CmdDone("cd3")
        assert cond.evaluate(Ctx()) is True
        assert cond.exit_code == 3, f"took the wrong command's code: {cond.exit_code}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
