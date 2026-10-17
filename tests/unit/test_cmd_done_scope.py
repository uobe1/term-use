#!/usr/bin/env python3
"""Unit tests for `CmdDone`'s marker authorization — the race a loaded machine exposed.

`send` takes its offset from the recording's current size, but the recorder lags
the pane: when the shell is busy, the *previous* command's `C` and `D` can both
land after that offset. Position then proves nothing about which command a marker
answers, and the tool reported `met` / `fact` / `exit 0` two seconds into a
`sleep 12`. A real tmux run reproduced that shape only under load, so it is pinned
here — including the two bindings that replace the position test (a drained
recording, the echo of what we typed) and the tmux 3.8 native reading that carries
the same bug in a different channel.

The fixtures are shaped like a real recording: i-events for what the client
injected, o-events for what the pane emitted, and the state.json keys `send`
actually writes (`last_send_offset`, `last_send_settled`, `native_baseline`).
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse.errors import TDError
from termuse.waiting import CmdDone, echo_barrier

C = "\x1b]133;C\x07"
D0 = "\x1b]133;D;0\x07"
D3 = "\x1b]133;D;3\x07"
A = "\x1b]133;A\x07"
B = "\x1b]133;B\x07"


class Ctx:
    """No tmux behind a fake session unless the test hands one a meta reading."""

    def __init__(self, meta=None):
        self._meta = meta

    def meta(self):
        if self._meta is None:
            raise TDError("session-missing", "no tmux in this test")
        return dict(self._meta)

    def capture(self, scrollback=0):
        return ""


def build(name, events, pre=0, settled=None, baseline="absent", marker=None):
    """Write a recording + state.json the way `send` leaves them.

    `events` is a list of ("o", text) pane-output payloads or ("i", injection)
    client records; `pre` is how many event lines sit *before* the offset, i.e.
    what the recorder had already flushed when `send` measured its window. Keep
    `pre` at 0 to reproduce the busy shape: the injection, the previous
    command's lagged C and D, and our own echo all land inside one window.
    Returns the temp root to clean up.
    """
    root = tempfile.mkdtemp(prefix="tu-cmddone-")
    os.environ["XDG_STATE_HOME"] = root
    from termuse import records
    path = records.session_dir(name) / "raw.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"version": "2", "width": 80, "height": 24,
                         "timestamp": 1.0})]
    for i, (code, data) in enumerate(events):
        lines.append(json.dumps([i * 0.1, code, data]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Byte offset of the line after the first `pre` event lines.
    cut = len("\n".join(lines[:1 + pre])) + 1
    state = {"session": name, "socket": None, "cmd": "bash",
             "shell_integration": True, "last_send_offset": cut}
    if settled is not None:
        state["last_send_settled"] = settled
    if marker is not None:
        state["marker_source"] = marker
    if baseline != "absent":
        state["native_baseline"] = baseline
    (path.parent / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return root


def inject(text):
    """The i-event `send --type TEXT --key Enter` writes for itself."""
    return ("i", [{"text": text}, {"key": "Enter"}])


def test_lagged_markers_for_the_previous_command_are_not_ours():
    # The busy shape: our line was injected, and the recorder then delivered the
    # previous command's C *and* D before our echo — both inside this window.
    root = build("cd1", [inject("sleep 12"),
                         ("o", C),
                         ("o", "old\r\n"),
                         ("o", D0 + A + B),
                         ("o", "$ sleep 12\r\n")],
                  pre=0, settled=False)
    try:
        cond = CmdDone("cd1")
        assert cond.evaluate(Ctx()) is False, \
            "a C..D pair the recorder lagged into our window belongs to the command before it"
        assert cond.exit_code is None and cond.via is None
        assert cond.degraded == "unattributed-marker", cond.degraded
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_the_same_window_answers_once_our_command_reports():
    # Same lag, then our command really finishes: our C sits after our echo, so
    # the echo is what makes it attributable — and it must still be a fact.
    root = build("cd2", [inject("sleep 12"),
                         ("o", C), ("o", "old\r\n"), ("o", D0 + A + B),
                         ("o", "$ sleep 12\r\n"),
                         ("o", C), ("o", "slept\r\n"), ("o", D3 + A + B)],
                  pre=0, settled=False)
    try:
        cond = CmdDone("cd2")
        assert cond.evaluate(Ctx()) is True, "our own C..D pair must still be fact"
        assert cond.exit_code == 3, f"took the wrong command's code: {cond.exit_code}"
        assert cond.via == "osc133" and cond.degraded is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_drained_recording_carries_a_keys_only_send():
    # Keys-only send (nothing to echo) with `send` having measured the file
    # quiet: the drain reading is the only binding available, so it must hold.
    root = build("cd3", [("i", [{"key": "Enter"}]),
                         ("o", B), ("o", C), ("o", "out\r\n"), ("o", D0 + A)],
                 pre=0, settled=True)
    try:
        cond = CmdDone("cd3")
        assert cond.evaluate(Ctx()) is True, "a settled window must not be refused"
        assert cond.exit_code == 0 and cond.via == "osc133"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_stopped_recorder_is_not_a_drained_one():
    # A paused recorder writes nothing, so "the file stopped growing" is True and
    # proves nothing. The echo still binds the marker, because stream order does
    # not depend on how fast the file is written: this is the shape e2e-31
    # produces with SIGSTOP, and the ordering rule that makes `_barrier` prefer
    # the echo over the drain reading.
    root = build("cd10", [inject("sleep 12"),
                          ("o", C), ("o", D0 + A + B),
                          ("o", "$ sleep 12\r\n")],
                  pre=0, settled=True)
    try:
        cond = CmdDone("cd10")
        assert cond.evaluate(Ctx()) is False, \
            "a quiet file is not proof; the borrowed C..D must still be refused"
        assert cond.degraded == "unattributed-marker", cond.degraded
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_an_unsettled_window_without_an_echo_proves_nothing():
    # The degraded residual: the recorder was still streaming when we injected
    # and the pane never echoed our keys, so no marker in the window can be
    # bound to this send. Degrading is the answer; a confident `fact` is not.
    root = build("cd4", [("i", [{"key": "Down"}]),
                         ("o", C), ("o", "menu\r\n"), ("o", D0 + A)],
                  pre=0, settled=False)
    try:
        cond = CmdDone("cd4")
        assert cond.evaluate(Ctx()) is False, "unattributable must not be a fact"
        assert cond.degraded == "unattributed-marker", cond.degraded
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_c_that_predates_the_window_is_still_rejected():
    # The easy variant, kept so both shapes fail loudly if the rule is deleted.
    root = build("cd5", [("o", C), ("o", "output"), ("o", D0 + A + B)],
                 pre=2, settled=True)
    try:
        cond = CmdDone("cd5")
        assert cond.evaluate(Ctx()) is False, \
            "a D whose C predates this send belongs to the command before it"
    finally:
        shutil.rmtree(root, ignore_errors=True)


NATIVE_BASE_BUSY = {"status": None, "end_time": None, "start_time": 100,
                    "running": 1}


def test_native_end_time_is_the_previous_command_while_it_was_running():
    # tmux 3.8, same class of lie in the other channel: we stamped the baseline
    # mid-flight, then the pane went idle because *that* command finished. Its
    # end_time is new, its start_time is not, and handing it back as a fresh
    # completion is what the byte scan was caught doing.
    root = build("cd6", [inject("sleep 12"), ("o", "$ sleep 12\r\n")],
                  pre=0, settled=False, baseline=NATIVE_BASE_BUSY)
    meta = {"pane_command_running": 0, "pane_command_status": 0,
            "pane_command_start_time": 100, "pane_command_end_time": 101}
    try:
        cond = CmdDone("cd6")
        assert cond.evaluate(Ctx(meta)) is False, \
            "a completion whose start_time is the baseline command's is not ours"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_native_reports_our_command_when_it_started_after_the_stamp():
    root = build("cd7", [inject("sleep 12"), ("o", "$ sleep 12\r\n")],
                  pre=0, settled=False, baseline=NATIVE_BASE_BUSY)
    meta = {"pane_command_running": 0, "pane_command_status": 0,
            "pane_command_start_time": 102, "pane_command_end_time": 103}
    try:
        cond = CmdDone("cd7")
        assert cond.evaluate(Ctx(meta)) is True
        assert cond.via == "tmux-command-status" and cond.exit_code == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_native_first_completion_after_an_idle_stamp_is_ours():
    # A session whose very first marker reaches tmux after our stamp: nothing can
    # be borrowed from before it, so the code is a fact even at one-second
    # timestamps.
    base = {"status": None, "end_time": None, "start_time": None, "running": 0}
    root = build("cd8", [inject("echo hi"), ("o", "$ echo hi\r\n")],
                 pre=0, settled=True, baseline=base)
    meta = {"pane_command_running": 0, "pane_command_status": 7,
            "pane_command_start_time": 50, "pane_command_end_time": 51}
    try:
        cond = CmdDone("cd8", expect_code=7)
        assert cond.evaluate(Ctx(meta)) is True, \
            "a first-ever completion after an idle stamp is the command we asked for"
        assert cond.exit_code == 7 and cond.via == "tmux-command-status"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_expect_code_mismatch_fails_immediately_on_either_channel():
    for settled, meta, channel in ((True, None, "byte"),
                                   (False, {"pane_command_running": 0,
                                             "pane_command_status": 1,
                                             "pane_command_start_time": 50,
                                             "pane_command_end_time": 51},
                                     "native")):
        root = build("cd9", [inject("false"), ("o", "$ false\r\n"),
                             ("o", B), ("o", C), ("o", "\x1b]133;D;1\x07" + A)],
                     pre=0, settled=settled,
                     baseline={"status": None, "end_time": None,
                               "start_time": None, "running": 0})
        try:
            try:
                CmdDone("cd9", expect_code=0).evaluate(Ctx(meta))
            except TDError as e:
                assert e.code == "expect-code-mismatch", (channel, e.code)
            else:
                raise AssertionError(f"mismatch must raise ({channel})")
        finally:
            shutil.rmtree(root, ignore_errors=True)


def test_a_hard_wrapped_echo_still_anchors_the_barrier():
    # Measured on the device: a wrapper's readline breaks the echoed line where
    # the cursor happens to sit — inside a token — so a match that tolerates only
    # collapsed whitespace misses our own echo, falls back to "the file was
    # quiet", and reports the borrowed pair as a fact.
    line = "printf '" + "a" * 32 + "'"
    nl, cr = chr(10), chr(13)
    wrapped = ("$ " + line[:15] + nl + line[15:26] + nl + line[26:] + cr + nl)
    assert wrapped.count("a") == 32
    root = build("cd11", [inject(line),
                          ("o", C), ("o", D0 + A),
                          ("o", wrapped)],
                 pre=0, settled=True)
    try:
        from termuse import records
        off = records.read_state("cd11")["last_send_offset"]
        assert echo_barrier("cd11", off, line), \
            "a hard line break inside a token must not hide our echo"
        cond = CmdDone("cd11")
        assert cond.evaluate(Ctx()) is False, \
            "once the echo is found, the borrowed C..D cannot be ours"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_tracked_send_waits_for_its_own_echo_even_if_the_file_looks_quiet():
    """The device race, in one fixture. The recorder was frozen, so `send`'s
    drain probe saw a flat file and wrote `last_send_settled: true` — but this
    send is one term-use narrates, so the missing echo is not "nothing lagged",
    it is our own line still in the pipe. Trusting the quiet file here answered
    `fact / exit 0` for a `sleep 12` that had not started."""
    events_lag = [inject("sleep 12"), ("o", C), ("o", D0 + A + B)]
    root = build("cd12", events_lag, pre=0, settled=True, marker="sentinel")
    try:
        cond = CmdDone("cd12")
        assert cond.evaluate(Ctx()) is False, \
            "a sentinel send must not fall back to the drain reading"
        assert cond.degraded == "unattributed-marker", cond.degraded
    finally:
        shutil.rmtree(root, ignore_errors=True)
    # ... and once the echo lands, the same window answers for our own command.
    root = build("cd12b", events_lag + [("o", "$ sleep 12\r\n"), ("o", C),
                                        ("o", D0 + A)],
                 pre=0, settled=True, marker="sentinel")
    try:
        cond = CmdDone("cd12b")
        assert cond.evaluate(Ctx()) is True, "the echo heals the race"
        assert cond.degraded is None and cond.exit_code == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
