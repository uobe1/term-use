#!/usr/bin/env python3
"""Quiet detection must not advance past a half-written event line.

The recorder appends JSON lines; a reader can observe the file after the bytes
of a line but before its newline. Advancing the scan offset to the file size at
that moment skips the event forever, so the next poll sees no activity and can
settle a pane that is still producing output. The offset must stop at the last
complete line and re-read the partial one once it is finished.
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import records, waiting


def main():
    root = tempfile.mkdtemp(prefix="tu-quiet-partial-")
    old = os.environ.get("XDG_STATE_HOME")
    os.environ["XDG_STATE_HOME"] = root
    try:
        d = records.session_dir("q1")
        d.mkdir(parents=True, exist_ok=True)
        header = json.dumps({"version": "2", "width": 80, "height": 24,
                             "timestamp": 1.0})
        first = json.dumps([0.1, "o", "first"])
        event2 = json.dumps([0.2, "o", "second"])
        cut = len(event2) // 2
        (d / "raw.log").write_text(
            header + "\n" + first + "\n" + event2[:cut], encoding="utf-8")
        (d / "state.json").write_text(
            json.dumps({"session": "q1", "last_send_offset": 0}),
            encoding="utf-8")

        quiet = waiting.QuietCondition("q1", 500, managed=True)
        assert quiet._last_o_ts == 0.1, quiet._last_o_ts

        with (d / "raw.log").open("a", encoding="utf-8") as fh:
            fh.write(event2[cut:] + "\n")
        quiet._consume_stream()
        assert quiet._last_o_ts == 0.2, \
            "the event that was partial on the previous poll was skipped"

        # A session whose recorder never produced an o-event has no Gate 1 to
        # read. A 3.8 server still knows the pane exists, so Gate 2 (screen
        # stability) may answer — but the verdict says the recorder was blind.
        blind = records.session_dir("q2")
        blind.mkdir(parents=True, exist_ok=True)
        (blind / "raw.log").write_text(header + "\n", encoding="utf-8")
        (blind / "state.json").write_text(
            json.dumps({"session": "q2", "last_send_offset": 0}),
            encoding="utf-8")

        class Ctx:
            def meta(self):
                return {"cursor_x": 0, "cursor_y": 0, "alternate_on": False,
                        "history_size": 0, "pane_command_running": 0}

            def capture(self, scrollback=0):
                return "same screen\n"

        quiet2 = waiting.QuietCondition("q2", 300, managed=True)
        assert quiet2.evaluate(Ctx()) is False
        assert quiet2.evaluate(Ctx()) is False, \
            "the quiet interval must still be honoured without Gate 1"
        time.sleep(0.3)
        assert quiet2.evaluate(Ctx()) is True
        assert quiet2.degraded == "recorder-blind"

        # A stream that writes only non-painting bytes is not a blind recorder:
        # Gate 1 has nothing to time, but the recorder is working, and the
        # verdict must say "nothing painted" (silent_unproven), not "blind".
        title = records.session_dir("q3")
        title.mkdir(parents=True, exist_ok=True)
        (title / "raw.log").write_text(
            header + "\n" + json.dumps([0.1, "o", "\x1b]0;spinner-1\x07"]) + "\n",
            encoding="utf-8")
        (title / "state.json").write_text(
            json.dumps({"session": "q3", "last_send_offset": 1}),
            encoding="utf-8")
        quiet3 = waiting.QuietCondition("q3", 300, managed=True)
        assert quiet3.evaluate(Ctx()) is False
        assert quiet3.evaluate(Ctx()) is False
        time.sleep(0.3)
        assert quiet3.evaluate(Ctx()) is True
        assert quiet3.degraded is None, quiet3.degraded
        assert quiet3.silent_unproven is True
    finally:
        if old is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = old
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
    print("test_quiet_partial: all passed")
