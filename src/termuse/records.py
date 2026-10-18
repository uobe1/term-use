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


# Key names that submit a line. Three readers need the same list (what is pending,
# what was asked to run, whether this send may carry a marker), and a fourth added
# here is cheaper than a fourth spelling of the same rule.
SUBMIT_KEYS = ("enter", "c-m", "c-j", "m-enter")


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


def drain_offset(session: str, settle_s: float = 0.05,
                 timeout_s: float = 0.6) -> tuple[int, bool]:
    """Where the recording ends right now, and whether the recorder had caught up.

    `send` needs a byte position that marks "nothing before this belongs to what
    I am about to cause". The file size is that position only if pipe.py has
    already flushed what the pane emitted *before* the injection — and it
    usually has not: the recorder lags the pane, so a just-finished command's
    trailing markers can land inside the next command's window.

    Two consecutive equal size reads mean the recorder is quiet, which is the
    strongest statement available on a server without 3.8's command tracking. A
    stream still growing at `timeout_s` (a spinner, a build) returns the size it
    reached plus False: the caller then knows its own offset is not trustworthy
    and must find another way to bind the marker to the send, or not claim fact.
    """
    path = session_dir(session) / "raw.log"
    deadline = time.monotonic() + timeout_s
    last = -1
    while True:
        try:
            size = path.stat().st_size
        except OSError:
            return 0, True          # no recording: there is nothing to lag behind
        if size == last:
            return size, True
        if time.monotonic() >= deadline:
            return size, False
        last = size
        time.sleep(settle_s)


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


# raw.log stores the stream as JSON strings, and JSON escapes control characters
# even with ensure_ascii=False — so on disk the marker reads `\u001b]133;A`, not
# the raw byte. Match both spellings rather than betting on which one appears.
# A prompt marker (A/B) proves the shell runs its own prompt cycle. A bare 133
# C/D may be term-use's own sentinel, so it is not proof of shell integration;
# any 633 marker is another tool's integration (our sentinel only writes 133).
_PROMPT_MARKERS = tuple(
    f"{esc}]{code};{letter}".encode()
    for esc in ("\\u001b", "\x1b") for code in ("133", "633") for letter in "AB")
_633_MARKERS = tuple(
    f"{esc}]{code};".encode()
    for esc in ("\\u001b", "\x1b") for code in ("633",))


def detect_shell_integration(session: str, wait_s: float = 0.0) -> bool:
    """Has this pane's shell run its own prompt cycle? Evidence, not assumption.

    `start` can only know what it injected itself; fish >= 4.0 emits OSC 133
    natively and a user's zsh/bash may carry VSCode's (633) or WezTerm's (133)
    integration. Refusing those sessions was a portability hole — the strongest
    evidence the tool offers was gated on a guess made at launch time.

    Proof is a prompt marker (A/B) or another tool's 633 stream. A bare 133 C/D
    can be term-use's own sentinel, which says the shell can be narrated, not
    that it reports for itself.
    """
    path = session_dir(session) / "raw.log"
    deadline = time.monotonic() + wait_s
    while True:
        try:
            size = path.stat().st_size
        except OSError:
            return False
        with path.open("rb") as fh:
            fh.seek(max(0, size - 65536))
            chunk = fh.read()
        if any(m in chunk for m in _PROMPT_MARKERS) \
                or any(m in chunk for m in _633_MARKERS):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def markers_seen(session: str, since_offset: int = 0, kinds=("D",)) -> bool:
    """Did the pane emit any of these OSC 133/633 markers in the stream range?

    A launch-time claim of shell integration is not a promise: a prompt
    framework that rewrites PROMPT_COMMAND (starship, powerline) takes the
    channel away mid-session. `kinds` defaults to `D` alone because `D` is the
    completion answer — `C` (from PS0) can keep flowing while `D` is dead, and
    counting both would miss exactly that case.
    """
    text = stream_since(session, since_offset)
    return any(f"\x1b]{code};{m}" in text
               for code in ("133", "633") for m in kinds)


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
                if str(ev["key"]).lower() in SUBMIT_KEYS:
                    typed = ""  # the line was submitted: nothing pending
    return typed or None


def last_sent_text(session: str, since_offset: int) -> str | None:
    """The text of the newest complete injection at or after that offset.

    `pending_input` answers "what is still sitting in the buffer"; this answers
    "what did the agent ask the shell to run", which is what an exit code has to
    be interpreted against. Keys are ignored — a line is its typed text.
    """
    last = None
    for _ts, code, data, _pos in events_since(session, since_offset):
        if code != "i" or not isinstance(data, list):
            continue
        text = "".join(ev["text"] for ev in data
                       if isinstance(ev, dict) and "text" in ev)
        if any(isinstance(ev, dict)
               and str(ev.get("key", "")).lower() in SUBMIT_KEYS for ev in data):
            last = text or None
    return last


def shell_markers_proven(session: str) -> bool:
    """Does this pane's shell run its own prompt cycle, without our help?

    term-use's completion sentinel writes a `C`+`D` pair, so a `D` somewhere in the
    stream proves a command finished — not that the *shell* will keep reporting
    them. A prompt marker (`A`/`B`, produced by PS1/PROMPT_COMMAND machinery, which
    is exactly what a wrapper like blerust takes over) together with a `D` is that
    proof, and it is what decides whether `send` has to append a sentinel at all.
    """
    path = session_dir(session) / "raw.log"
    try:
        size = path.stat().st_size
    except OSError:
        return False
    with path.open("rb") as fh:
        fh.seek(max(0, size - 65536))
        chunk = fh.read()

    def seen(*letters: str) -> bool:
        return any(f"{esc}]{code};{letter}".encode() in chunk
                   for esc in ("\\u001b", "\x1b") for code in ("133", "633")
                   for letter in letters)

    return seen("A", "B") and seen("D")


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
