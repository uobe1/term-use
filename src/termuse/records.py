"""On-disk session evidence: asciicast v2 raw.log + atomic state.json.

Layout: $XDG_STATE_HOME/term-use/<session>/
  raw.log     line 1 = v2 header (object); then event lines [elapsed, code, data]
              codes: o=pane output stream, i=client injection, r=resize, m=sync point
  state.json  locator + recorder state (socket, pane_id, shell_integration,
              last_send_offset ...); always written via tmp+rename.
"""
import json
import os
import time
from pathlib import Path

from termuse.errors import TDError


def state_root() -> Path:
    """XDG Base Directory spec: session recordings + state are logs/current
    state (XDG_STATE_HOME), not discardable cache (XDG_CACHE_HOME)."""
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "term-use"


def session_dir(session: str) -> Path:
    return state_root() / session


def read_state(session: str) -> dict:
    p = session_dir(session) / "state.json"
    if not p.exists():
        raise TDError("session-missing",
                      f"no state.json for session {session!r}",
                      hint=f"expected at {p} — if you started the session with a "
                           f"different XDG_STATE_HOME, every command must use the "
                           f"same one; or start with: term-use start -n "
                           f"{session} --cmd <command>")
    return json.loads(p.read_text())


def events_since(session: str, offset: int) -> list[tuple]:
    """(elapsed, code, data, byte_pos) tuples from raw.log.

    byte_pos is the file offset of the event's own line, so callers can
    correlate events with state.last_send_offset. Starting at 0 replays the
    whole session (needed to keep OSC 133 state machine continuity).
    """
    path = session_dir(session) / "raw.log"
    out: list[tuple] = []
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return out  # unmanaged session (nested tmux) or cache mismatch
    with fh:
        pos = fh.seek(offset)
        for line in fh:
            start = pos
            pos += len(line.encode("utf-8", "replace"))
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, list) and len(ev) >= 3 and isinstance(ev[1], str):
                out.append((ev[0], ev[1], ev[2], start))
    return out


def stream_since(session: str, offset: int) -> str:
    """Concatenate o-event payloads from raw.log starting at byte offset."""
    return "".join(data for _, code, data, _ in events_since(session, offset)
                   if code == "o" and isinstance(data, str))


def header(session: str) -> dict | None:
    """The v2 header line (carries the wall-clock timestamp the elapsed
    seconds are measured from), or None when there is no recording."""
    try:
        with (session_dir(session) / "raw.log").open("r", encoding="utf-8",
                                                     errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return None
    try:
        head = json.loads(first)
    except ValueError:
        return None
    return head if isinstance(head, dict) else None


def last_event_ts(session: str, code: str = "o") -> float | None:
    """Elapsed time of the newest event of `code`, read from the file tail.

    A tail read (not a full replay) matters: this is called while a session
    may have megabytes of recording, and an age signal must cost microseconds.
    """
    path = session_dir(session) / "raw.log"
    try:
        size = path.stat().st_size
    except OSError:
        return None
    start = max(0, size - 65536)
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            chunk = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    lines = chunk.splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # first line is a partial read
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if (isinstance(ev, list) and len(ev) >= 3
                and ev[1] == code and isinstance(ev[0], (int, float))):
            return float(ev[0])
    return None


def output_age_s(session: str) -> float | None:
    """Seconds since the pane last emitted anything (None if unmeasurable:
    no recording, or nothing ever written)."""
    head = header(session)
    last = last_event_ts(session, "o")
    if head is None or last is None or head.get("timestamp") is None:
        return None
    return max(0.0, round(time.time() - head["timestamp"] - last, 2))


def pending_input(session: str, since_offset: int) -> str | None:
    """Text injected after the last Enter — the "half-typed line" signal.

    When a remote shell behind the pane stops reading (a hung ssh session, a
    program that swallowed the keystrokes), the typed text still echoes on the
    input row, so a plain screen read looks like progress. Reporting what the
    client injected without ever completing it lets an agent tell "no output"
    apart from "my input never landed" instead of guessing with a C-c.
    """
    typed = ""
    for _ts, code, data, _pos in events_since(session, since_offset):
        if code != "i" or not isinstance(data, list):
            continue
        for ev in data:
            if not isinstance(ev, dict):
                continue
            if "text" in ev:
                typed += ev["text"]
            elif "key" in ev:
                if str(ev["key"]).lower() in ("enter", "c-m", "c-j", "m-enter"):
                    typed = ""  # the line was submitted: nothing pending
    return typed or None


class V2Writer:
    """Appends events to raw.log and persists state.json atomically."""

    def __init__(self, session: str):
        self.dir = session_dir(session)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "raw.log"
        self.state_path = self.dir / "state.json"
        header_ts = None
        if self.path.exists():
            first = self.path.read_bytes().split(b"\n", 1)[0]
            try:
                header_ts = json.loads(first).get("timestamp")
            except (ValueError, AttributeError):
                header_ts = None
        self.t0 = header_ts if header_ts is not None else time.time()

    @classmethod
    def fresh(cls, session: str, width: int, height: int, cmd: str,
              socket: str | None) -> "V2Writer":
        w = cls(session)
        w.t0 = time.time()
        header = {
            "version": 2,
            "width": width,
            "height": height,
            "timestamp": w.t0,
            "cmd": cmd,
            "env": {k: os.environ.get(k) for k in ("SHELL", "TERM")},
        }
        w.path.write_text(json.dumps(header, ensure_ascii=False) + "\n")
        w.state = {
            "session": session,
            "socket": socket,
            "pane_id": None,
            "cmd": cmd,
            "width": width,
            "height": height,
            "shell_integration": False,
            "last_send_offset": 0,
            "started_at": w.t0,
        }
        w.save_state()
        return w

    def save_state(self) -> None:
        tmp = self.state_path.with_name("state.json.tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False) + "\n")
        os.replace(tmp, self.state_path)

    def append(self, code: str, data) -> None:
        line = json.dumps([round(time.time() - self.t0, 6), code, data],
                          ensure_ascii=False) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0
