"""tmux primitives: target parsing, command assembly, capture and pane metadata.

The locator is a (socket, session, window, pane) four-tuple. Accepts the
following CLI spellings (socket omitted -> default tmux server):

  s              -> (None, s, None, None)
  s:1            -> (None, s, "1", None)
  s:1.0          -> (None, s, "1", "0")
  sock:s         -> (sock, s, None, None)
  sock:s:1.0     -> (sock, s, "1", "0")
"""
import signal
import subprocess
from dataclasses import dataclass

from termuse.errors import TDError


def ok(res: subprocess.CompletedProcess) -> bool:
    """Did tmux actually do it? Exit code alone is not enough.

    Measured on tmux 3.7c: a client that cannot reach its server (a socket
    owned by another security context in a shared /tmp/tmux-<uid>) prints
    "access not allowed" and still exits 0. Trusting the code alone made
    `start` answer "session already exists" for a server it never contacted,
    and made `capture-pane` answer "the screen is empty" for a failed read.
    """
    return res.returncode == 0 and not (res.stderr or "").strip()


# stderr fingerprint -> (error code, what the agent can do about it)
_FAILURE_PATTERNS = (
    (("access not allowed", "permission denied", "operation not permitted",
      "connect to server failed", "connection refused", "lost server",
      "server exited", "protocol version", "couldn't create directory",
      "not a directory", "no such file or directory"),
     "socket-unreachable",
     "the tmux server socket is not usable from here. On a shared machine the "
     "default socket under /tmp/tmux-<uid> may belong to another user or "
     "security context: pass --socket NAME (a private socket) or point "
     "TMUX_TMPDIR at a directory you own. TMUX_TMPDIR inherited from another "
     "device (e.g. a Termux profile on a non-Android box) does the same."),
    (("no server running", "no sessions", "can't find session",
      "can't find instance", "no current session", "session not found",
      "can't find pane", "can't find window", "invalid session id",
      "no such session"),
     "session-missing",
     "list what is there with: term-use sessions [--socket NAME], or start a "
     "fresh one: term-use start -n NAME --cmd bash"),
)


def classify(res: subprocess.CompletedProcess) -> tuple[str, str | None]:
    """Map tmux's stderr onto our error taxonomy; 'tmux-failed' = unknown."""
    msg = (res.stderr or "").strip().lower()
    for needles, code, hint in _FAILURE_PATTERNS:
        if any(n in msg for n in needles):
            return code, hint
    return "tmux-failed", None


def tmux(*args: str, socket: str | None = None) -> subprocess.CompletedProcess:
    """Run a tmux command; never raises, callers inspect the result."""
    cmd = ["tmux", *(["-L", socket] if socket else []), *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise TDError("socket-unreachable", "tmux binary not found on PATH",
                      hint="install tmux with the system package manager "
                           "(the only hard dependency of this tool)")


def require(res: subprocess.CompletedProcess, err_code: str = "session-missing",
            hint: str | None = None) -> str:
    """Return stdout of a tmux call or raise a structured error.

    An unclassified tmux failure keeps the caller's error code (callers know
    what they were attempting); a classified one reports what the fingerprint
    actually says, because "session-missing" for a socket you cannot reach is
    a wrong diagnosis that sends agents off to restart sessions.
    """
    msg = (res.stderr or "").strip()
    if ok(res):
        return res.stdout
    code, auto = classify(res)
    if code == "tmux-failed":
        code = err_code
    raise TDError(code, msg or f"tmux failed (exit {res.returncode})",
                  hint=hint or auto)


@dataclass(frozen=True)
class Target:
    socket: str | None
    session: str
    window: str | None
    pane: str | None

    def to_arg(self) -> str:
        t = self.session
        if self.window is not None:
            t += f":{self.window}"
            if self.pane is not None:
                t += f".{self.pane}"
        return t


def parse_target(spec: str) -> Target:
    parts = spec.split(":")
    if len(parts) == 1:
        socket, sess, win = None, parts[0], None
    elif len(parts) == 2:
        socket, sess, win = None, parts[0], parts[1]
    elif len(parts) == 3:
        socket, sess, win = parts[0], parts[1], parts[2]
    else:
        raise TDError("session-missing", f"malformed target {spec!r}", hint="use [socket:]session[:window[.pane]]")
    if not sess:
        raise TDError("session-missing", f"target {spec!r} names no session")
    if win and "." in win:
        win, pane = win.split(".", 1)
    else:
        pane = ""
    return Target(socket or None, sess, win or None, pane or None)


def capture(target: Target, flags: tuple[str, ...] = ()) -> str:
    """Visible screen text (plus history with flags like ("-S", "-50)")."""
    res = tmux("capture-pane", *flags, "-p", "-t", target.to_arg(), socket=target.socket)
    return require(res)


def signal_name(value: str) -> str:
    """Normalize a dead-pane signal to its name.

    tmux answers `#{pane_dead_signal}` with a name on some builds and a bare
    number on others (measured: "9" on tmux 3.7c/Linux, "KILL" on the Termux
    device the harness was written against) — agents must not have to know
    which their server speaks.
    """
    if value.isdigit():
        try:
            return signal.Signals(int(value)).name.removeprefix("SIG")
        except ValueError:  # not a signal this platform knows
            return value
    return value


def meta(target: Target) -> dict:
    """One-call pane status: cursor, modes, history, death, size, cwd."""
    keys = ("cursor_x", "cursor_y", "alternate_on", "pane_in_mode",
            "history_size", "pane_dead", "pane_dead_status", "pane_dead_signal",
            "pane_width", "pane_height", "pane_current_command",
            "pane_current_path")
    fmt = "|".join("#{" + k + "}" for k in keys)
    res = tmux("display-message", "-p", "-F", fmt, "-t", target.to_arg(), socket=target.socket)
    out = require(res).rstrip("\n").split("|")
    d: dict = {}
    bool_keys = {"alternate_on", "pane_in_mode", "pane_dead"}
    int_keys = {"cursor_x", "cursor_y", "history_size", "pane_dead_status",
                "pane_width", "pane_height"}
    for i, k in enumerate(keys):
        # pane_current_path is last and may itself contain "|": rejoin tail.
        if k == "pane_current_path" and len(out) > len(keys) - 1:
            v = "|".join(out[len(keys) - 1:])
        else:
            v = out[i] if i < len(out) else ""
        if v == "":
            d[k] = None
        elif k in bool_keys:
            d[k] = v == "1"
        elif k in int_keys:
            try:
                d[k] = int(v)
            except ValueError:
                d[k] = v  # unexpected tmux answer: report it, never crash
        else:  # e.g. pane_dead_signal is a name like "KILL"
            d[k] = v
    if d.get("pane_dead_signal"):
        d["pane_dead_signal_name"] = signal_name(d["pane_dead_signal"])
    return d
