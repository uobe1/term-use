#!/usr/bin/env python3
"""Unit tests for `records.last_sent_text` — the line an exit code is read against.

A throwaway XDG_STATE_HOME with a hand-written raw.log: the reader must agree with
the recorder's format, which is exactly the thing that rots silently when either
side changes, so it is pinned here rather than left to the e2e happy path.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def session(name, lines):
    root = tempfile.mkdtemp(prefix="tu-records-")
    os.environ["XDG_STATE_HOME"] = root
    from termuse import records
    records.session_dir(name).mkdir(parents=True, exist_ok=True)
    body = [json.dumps({"version": "2", "width": 80, "height": 24, "timestamp": 1.0})
            + "\n"]
    pos = [len(body[0].encode())]          # byte offset each line starts at
    starts = {}
    for i, _ in enumerate(lines):
        starts[i] = pos[i]
        pos.append(pos[i] + len((lines[i] + "\n").encode()))
    (records.session_dir(name) / "raw.log").write_text(
        body[0] + "".join(lines[i] + "\n" for i in range(len(lines))), encoding="utf-8")
    return root, records, starts


def ev(ts, code, data):
    return json.dumps([ts, code, data], ensure_ascii=False)


def test_returns_the_last_submitted_injection():
    lines = [ev(0.1, "i", [{"text": "first"}, {"key": "Enter"}]),
             ev(0.2, "o", "output"),
             ev(0.3, "i", [{"text": "second && cat log"}, {"key": "Enter"}])]
    root, records, starts = session("u1", lines)
    try:
        assert records.last_sent_text("u1", starts[2]) == "second && cat log"
        assert records.last_sent_text("u1", starts[0]) == "second && cat log"
    finally:
        records, root = None, root


def test_unsubmitted_text_is_not_a_sent_line():
    # An injection without Enter never ran, so there is no line to interpret.
    lines = [ev(0.1, "i", [{"text": "half typed"}])]
    root, records, starts = session("u2", lines)
    try:
        assert records.last_sent_text("u2", 0) is None
    finally:
        del root


def test_repeats_answer_with_the_last_repetition():
    lines = [ev(0.1, "i", [{"text": "k"}, {"key": "Enter"}]),
             ev(0.2, "i", [{"text": "k"}, {"key": "Enter"}])]
    root, records, starts = session("u3", lines)
    try:
        assert records.last_sent_text("u3", starts[1]) == "k"
    finally:
        del root


def test_missing_recording_is_silently_none():
    root = tempfile.mkdtemp(prefix="tu-records-")
    os.environ["XDG_STATE_HOME"] = root
    from termuse import records
    assert records.last_sent_text("never-started", 0) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
