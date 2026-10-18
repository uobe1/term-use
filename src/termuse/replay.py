"""Reconstructing what the screen showed, from the recorded bytes.

Two engines, because they answer different questions:

  `linear()`   cheap, no server contact. Strip the escape sequences and honour
               \\r/\\n, so a command's output reads as the text it was. Honest
               for line-oriented programs; a full-screen program positions its
               glyphs with cursor moves, which a linear reader cannot follow —
               `looks_redrawn()` says when that happened instead of letting the
               text quietly lie.

  `frame()`    exact. Feed the recorded bytes back through **tmux itself**, in
               a throwaway pane on a private socket, and capture what it paints.
               tmux is the emulator that produced the recording, so there is no
               second implementation to disagree with — which is why this is
               here instead of pyte: it diverges on modern sequences, has no
               alt-screen, and drops data on ZWJ emoji.

The scratch pane runs `stty raw -echo; cat`: writing to a pane's tty delivers
bytes to the program's *stdin*, so something inside has to echo them back out
as real output, and `-echo` keeps the line discipline from doubling them.
"""
import os
import re
import time

from termuse import records, screen, tmuxio
from termuse.errors import TDError


# What makes linear text a *lie* about the frame: a program that switches to the
# alternate screen, clears, or moves the cursor to repaint cells. Deliberately
# narrow — an earlier substring list matched `\x1b[1;` (bold SGR: every colored
# Python traceback) and a bare `\x1b[H` (bash repainting its own prompt on C-l);
# that false positive sends the reader on a `replay` detour for no reason.
# A bare `\x1b[2J` went the same way: `clear` in a plain shell emits it, and the
# warning then told an agent not to trust a correct read of an artifact file.
# A program that really repaints still matches: alt-screen, absolute positioning
# or cursor-up. Bracketed paste (`?2004`) and color codes are not redraw.
_REDRAW_RE = re.compile(
    r"\x1b\[\?(?:47|1047|1049)h"      # enter alternate screen
    r"|\x1b\[\d+;\d+H"                 # absolute cursor position
    r"|\x1b\[\d+A")                    # cursor up (in-place repaint)


def strip_escapes(text: str) -> str:
    """Escape sequences out, \\r and \\n kept (they carry the layout)."""
    return screen.strip_escapes(text)


def looks_redrawn(raw: str) -> bool:
    return bool(_REDRAW_RE.search(raw))


def linear(raw: str) -> str:
    """Byte stream -> the text a line-oriented program put on screen.

    A \\r rewinds the column and later characters overwrite what was there, so
    a progress bar collapses to its final state instead of becoming one long
    line of stale frames.
    """
    text = strip_escapes(raw)
    lines: list[list[str]] = [[]]
    col = 0

    def put(ch_or_space: str) -> None:
        nonlocal col
        line = lines[-1]
        while len(line) <= col:
            line.append(" ")
        line[col] = ch_or_space
        col += 1

    for ch in text:
        if ch == "\n":
            lines.append([])
            col = 0
        elif ch == "\r":
            col = 0
        elif ch == "\t":
            for _ in range(8 - col % 8):
                put(" ")
        elif ch == "\x08":
            col = max(0, col - 1)         # backspace moves, it does not delete
        elif ch in ("\x00", "\x07"):
            continue
        else:
            put(ch)
    return "\n".join("".join(l).rstrip() for l in lines).rstrip("\n")


def trim(text: str, max_bytes: int) -> tuple[str, int]:
    """Head + tail within a byte budget, with the elision marked.

    The marker is the point: an agent that silently receives half a log cannot
    tell truncation from a program that stopped printing.
    """
    raw = text.encode("utf-8", "replace")
    if not max_bytes or len(raw) <= max_bytes:
        return text, 0
    half = max(1, max_bytes // 2)
    omitted = len(raw) - 2 * half
    marker = (f"\n[... term-use: limited to {max_bytes} bytes, {omitted} "
              f"interior bytes omitted (--max-bytes 0 for everything) ...]\n")
    head = raw[:half].decode("utf-8", "replace")
    tail = raw[-half:].decode("utf-8", "replace")
    return head + marker + tail, omitted


def _segments(session: str, offset: int | None, elapsed: float | None):
    """Recorded output split at resize points: [(text, (cols, rows) | None)].

    A resize reflows what is already on the screen, so replaying it out of
    order would paint a different frame than the one that was recorded.
    """
    segs: list[tuple[str, tuple | None]] = []
    buf, geom = "", None
    for ts, code, data, pos in records.events_since(session, 0):
        if elapsed is not None and ts > elapsed:
            break
        if code == "r" and isinstance(data, dict):
            if buf:
                segs.append((buf, geom))
                buf = ""
            # Geometry is cumulative state, not scoped output: a resize that
            # happened before `--at last-send` still decides the width of the
            # frame that starts there, so it is read regardless of the offset.
            geom = (data.get("cols"), data.get("rows"))
            continue
        if code == "o" and isinstance(data, str):
            if offset is not None and pos < offset:
                continue
            buf += data
    if buf:
        segs.append((buf, geom))
    return segs


def resolve_at(session: str, at: str) -> tuple[dict, float | None, int | None]:
    """`--at` accepts elapsed seconds, `offset:N`, `last-send` or `start`."""
    state = records.read_state(session)
    head = records.header(session) or {}
    if at in ("last-send", "send"):
        return head, None, int(state.get("last_send_offset", 0))
    if at == "start":
        return head, None, 0
    if at.startswith("offset:"):
        try:
            return head, None, int(at.split(":", 1)[1])
        except ValueError:
            raise TDError("invalid-arguments", f"--at {at!r}: offset must be an integer")
    try:
        return head, float(at), None
    except ValueError:
        raise TDError(
            "invalid-arguments",
            f"--at {at!r} is not a number of seconds, offset:N, start or last-send",
            hint="seconds are measured from the session start recorded in "
                 "raw.log's header; `trace -n NAME` prints them per event")


def frame(session: str, *, elapsed: float | None = None, offset: int | None = None,
          cols: int = 80, rows: int = 24, settle_s: float = 5.0) -> tuple[str, dict]:
    """The pane's own `capture-pane -e` output after replaying up to a point,
    plus what the reconstructed pane reports about itself (cursor, alternate
    screen, and the geometry the replay actually ended on)."""
    segs = _segments(session, offset, elapsed)
    total = sum(len(s[0]) for s in segs)
    sock = f"tu-replay-{os.getpid()}"
    try:
        res = tmuxio.tmux("new-session", "-d", "-s", "rp", "-x", str(cols),
                          "-y", str(rows),
                          "sh -c 'stty raw -echo; exec cat'", socket=sock)
        tmuxio.require(res, "socket-unreachable")
        # Wait for `cat` to be running before writing: bytes that arrive while
        # the outer `sh` is still executing stty/exec get eaten by a process
        # that is about to be replaced, which shows up as a frame that silently
        # lost its beginning.
        ready = time.monotonic() + settle_s
        while True:
            cur = tmuxio.tmux("display-message", "-p", "-t", "rp",
                              "-F", "#{pane_current_command}", socket=sock)
            if "cat" in cur.stdout:
                break
            if time.monotonic() > ready:
                raise TDError(
                    "timeout",
                    f"the scratch replay pane never reached cat "
                    f"(it reports {cur.stdout.strip()!r})",
                    hint="the tmux server may be short on resources; retry, or "
                         "read the bytes with: term-use out -n " + session)
            time.sleep(0.02)
        tty = tmuxio.tmux("display-message", "-p", "-t", "rp",
                          "-F", "#{pane_tty}", socket=sock)
        tmuxio.require(tty, "session-missing")
        tty_path = tty.stdout.strip()
        if not tty_path:
            raise TDError("tmux-failed",
                          "tmux reported no pane_tty for the scratch pane")
        with open(tty_path, "wb", buffering=0) as fh:
            for text, geom in segs:
                if geom and geom[0] and geom[1]:
                    tmuxio.tmux("resize-window", "-t", "rp", "-x", str(geom[0]),
                                "-y", str(geom[1]), socket=sock)
                fh.write(text.encode("utf-8", "replace"))
            # Settle by watching the screen stop changing. The obvious trick —
            # append a sentinel and wait to see it — perturbs the very frame we
            # are trying to reconstruct (it scrolls a line in and shifts the
            # trailing blanks), so the answer has to come from observation.
            prev = None
            stable = 0
            deadline = time.monotonic() + settle_s
            while time.monotonic() < deadline:
                time.sleep(0.1)
                cap = tmuxio.tmux("capture-pane", "-p", "-t", "rp", socket=sock)
                if cap.stdout == prev:
                    stable += 1
                    if stable >= 2:
                        break
                else:
                    stable = 0
                prev = cap.stdout
            else:
                raise TDError(
                    "timeout",
                    f"the scratch pane had not finished replaying {total} bytes "
                    f"within {settle_s}s",
                    hint="raise --timeout, or read the recorded bytes directly "
                         f"with: term-use out -n {session}")
        cap = tmuxio.tmux("capture-pane", "-p", "-e", "-N", "-t", "rp",
                          socket=sock)
        text = tmuxio.require(cap, "session-missing")
        # A frame is a screen *and* a cursor: tmux knows where the reconstructed
        # pane left its cursor and whether the program was on the alternate
        # screen, which is what lets a replayed picture show where the program
        # actually was — and lets the caller take the geometry from the replay
        # instead of trusting the header it started with.
        cur = tmuxio.tmux("display-message", "-p", "-t", "rp", "-F",
                          "#{cursor_x}|#{cursor_y}|#{alternate_on}"
                          "|#{pane_width}|#{pane_height}", socket=sock)
    finally:
        tmuxio.tmux("kill-server", socket=sock)
    parts = tmuxio.require(cur, "session-missing").rstrip("\n").split("|")

    def num(i: int) -> int | None:
        try:
            return int(parts[i])
        except (IndexError, ValueError):
            return None

    return text, {"cursor_x": num(0), "cursor_y": num(1),
                  "alternate_on": parts[2] == "1" if len(parts) > 2 else None,
                  "pane_width": num(3), "pane_height": num(4)}

