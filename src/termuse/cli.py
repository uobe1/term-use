"""term-use v2 CLI: evidence-driven driving/debugging of interactive terminals.

Client process is stateless and short-lived; tmux server is the service.
All failures surface as single-line JSON errors (termuse.errors.TDError).
"""
import argparse
import json
import os
import re
import shlex
import sys
import tempfile
import time
from pathlib import Path

from termuse import __version__
from termuse import budget as tdbudget
from termuse import input as tdinput
from termuse import rcfile, records, replay, screen as tdscreen
from termuse import screenshot as tdscreenshot
from termuse import tmuxio, waiting
from termuse.errors import TDError

PIPE_SCRIPT = Path(__file__).resolve().parent / "pipe.py"


def resolve_target(name: str, socket: str | None = None) -> tmuxio.Target:
    """Resolve a CLI -n argument to a full target; socket falls back to state.json.

    An explicit socket in the target (inner:inner:0) wins — nested tmux panes
    are addressable without being term-use-managed. A bare name addresses
    the default server like plain tmux would: if the session exists there
    without term-use records, it is returned unmanaged (waits degrade)
    instead of refusing; state.json remains the fallback for sessions on
    custom sockets.
    """
    t = tmuxio.parse_target(name)
    if socket is not None and t.socket is None:
        t = tmuxio.Target(socket, t.session, t.window, t.pane)
    elif t.socket is None:
        try:
            sock = records.read_state(t.session).get("socket")
        except TDError as e:
            if not tmuxio.ok(tmuxio.tmux("has-session", "-t", t.session)):
                # Most common real cause: the session lives on another tmux
                # socket and was addressed by its bare name. Say so — the
                # XDG_STATE_HOME hint alone sends agents hunting the wrong file.
                raise TDError(
                    e.code, e.message,
                    hint=(e.hint or "") + " If the session runs on a named tmux "
                         "socket, address it as sock:name[:win.pane] or pass "
                         "--socket NAME; see what exists with: term-use "
                         "sessions --socket NAME")
            sock = None  # exists unmanaged on the default server
        t = tmuxio.Target(sock, t.session, t.window, t.pane)
    return t


def load_state_or_none(session: str) -> dict | None:
    try:
        return records.read_state(session)
    except TDError:
        return None


def cmd_sessions(args) -> int:
    res = tmuxio.tmux("list-sessions", "-F",
                      "#{session_name}|#{session_windows}|#{session_attached}",
                      socket=args.socket)
    rows = []
    if tmuxio.ok(res):
        for line in res.stdout.splitlines():
            parts = line.split("|")
            if len(parts) >= 1 and parts[0]:
                rows.append({"name": parts[0],
                             "windows": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None,
                             "attached": parts[2:] and parts[2] == "1"})
    else:
        # "no server running" is an honest empty list; so is tmux's other answer
        # for a private socket that has never been started (`error connecting to
        # … (No such file or directory)`) — the recommended workflow says "use a
        # private --socket", and a caller that sees rc=1 there will read it as an
        # environment fault before its first `start`. Anything else tmux
        # complains about must still surface, not be reported as an empty room.
        msg = (res.stderr or "").strip()
        low = msg.lower()
        if "no server running" in low or "no sessions" in low:
            rows = []
        elif "no such file or directory" in low and "error connecting" in low:
            rows = []
            # Two states look identical to tmux here: nothing has ever been
            # started on this socket, or a running server had its socket file
            # removed underneath it. Both have been measured — a live server
            # PID and no socket file. Say both, with the check.
            print(f"note: no socket file for {args.socket!r}. Either nothing has "
                  f"been started on it yet — start one with `term-use start -n "
                  f"NAME --socket {args.socket}` — or a running server lost its "
                  f"socket: `pgrep -aft 'tmux.*{args.socket}'` shows whether a "
                  f"server is alive, and `tmux -L {args.socket} kill-server` "
                  f"clears an orphan. Listing is empty either way, not failed.",
                  file=sys.stderr)
        else:
            code, hint = tmuxio.classify(res)
            raise TDError(code, msg or f"tmux failed (exit {res.returncode})",
                          hint=hint)
    if getattr(args, "name", None):
        rows = [r for r in rows if r["name"] == args.name]
    print(json.dumps(rows, ensure_ascii=False))
    return 0


def cmd_start(args) -> int:
    if tmuxio.ok(tmuxio.tmux("has-session", "-t", args.name, socket=args.socket)):
        # ok(), not returncode: a tmux client that cannot reach its server can
        # exit 0 with an error on stderr, and "already exists" would be a lie
        # that sends agents off to kill a session that is not there.
        raise TDError("session-missing",
                      f"session {args.name!r} already exists",
                      hint="pick another -n name or stop the existing session")
    writer = records.V2Writer.fresh(args.name, args.width, args.height,
                                    args.cmd, args.socket)
    # Shell integration: an *interactive* bash gets the injected rcfile
    # (OSC 133). Matching only the program name used to silently swallow
    # `--cmd "bash script.sh"` — the script argument vanished and a bare shell
    # took its place. Anything that is not a plain interactive bash (a script
    # operand, a `-c` command string, or the operator's own --rcfile) is left
    # exactly as asked for.
    cmd = args.cmd
    words = cmd.split()
    base = words[0].rsplit("/", 1)[-1] if words else ""
    owns_rc = "--rcfile" in words or "--init-file" in words
    if base == "bash" and waiting.shell_owns_the_pane(cmd) and not owns_rc:
        rc = writer.dir / "bashrc"
        rc.write_text(rcfile.bashrc_template())
        extra = "".join(f" {shlex.quote(w)}" for w in words[1:])
        cmd = f"{words[0]} --rcfile {shlex.quote(str(rc))} -i{extra}"
        writer.state["shell_integration"] = True
        writer.save_state()
    elif base == "zsh" and waiting.shell_owns_the_pane(cmd):
        # A ZDOTDIR of our own that sources the user's files first, so their
        # .zshrc, their precmd() and their history location all survive.
        zd = writer.dir / "zdotdir"
        zd.mkdir(parents=True, exist_ok=True)
        for fname, body in rcfile.zsh_integration_files().items():
            (zd / f".{fname}").write_text(body)   # zsh wants the leading dot
        extra = "".join(f" {shlex.quote(w)}" for w in words[1:])
        cmd = f"env ZDOTDIR={shlex.quote(str(zd))} {words[0]}{extra}"
        writer.state["shell_integration"] = True
        writer.save_state()
    # remain-on-exit must be set *inside* the pane before exec: short-lived
    # commands would otherwise die (and take the session with them) before the
    # external set-option lands.
    #
    # The gate exists for evidence completeness: the target must not run before
    # pipe-pane is attached. Without it a program that prints at startup loses
    # its opening bytes (measured: the first line of a reproduction was missing
    # from raw.log), so the recording silently lies about what the pane showed.
    # `stty -echo` keeps the release keystroke from painting a blank line first.
    gate = "stty -echo 2>/dev/null; read __tu_gate; stty echo 2>/dev/null; "
    inner = f"tmux set-option -w remain-on-exit on; {gate}exec {cmd}"
    res = tmuxio.tmux("new-session", "-d", "-s", args.name,
                      "-x", str(args.width), "-y", str(args.height),
                      inner, socket=args.socket)
    tmuxio.require(res, "socket-unreachable")
    pane_id = tmuxio.require(
        tmuxio.tmux("display-message", "-p", "-t", args.name, "-F", "#{pane_id}",
                    socket=args.socket)).strip()
    writer.state["pane_id"] = pane_id
    writer.save_state()
    # Recorder: pane output stream -> raw.log as v2 o-events.
    raw = shlex.quote(str(writer.dir / "raw.log"))
    tmuxio.tmux("pipe-pane", "-o", "-t", pane_id,
                f"{sys.executable} {PIPE_SCRIPT} {raw}", socket=args.socket)
    attached = False
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        p = tmuxio.tmux("display-message", "-p", "-t", pane_id, "-F",
                        "#{pane_pipe}", socket=args.socket)
        if p.stdout.strip() == "1":
            attached = True
            break
        time.sleep(0.02)
    # Release the gate. C-d, not Enter: if the byte reaches the tty before
    # `stty -echo` has run, an Enter would echo a blank line into the top of the
    # pane (and into the recording), while EOF at an empty line is never
    # echoed and `read` returns on it either way.
    time.sleep(0.05)
    tdinput.send(tmuxio.Target(args.socket, args.name, None, None), [],
                 ["C-d"], [])
    writer.append("m", {"event": "start", "recorder": attached})
    result = {"ok": True, "session": args.name, "socket": args.socket,
              "pane_id": pane_id, "width": args.width,
              "height": args.height, "recorder": attached}
    if not attached:
        result["warning"] = "recorder-not-attached"
        print("warning: pipe-pane did not come up, so raw.log will not record "
              "this session's bytes; the screen channels still work. Check that "
              f"{sys.executable} is runnable by the tmux server.",
              file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_stop(args) -> int:
    state = load_state_or_none(args.name)
    if state is None:
        # Degraded cleanup: records are unreachable (cache mismatch), but the
        # tmux session itself can still be killed — cleanup must never require
        # forensics. Only the session *name* is known without state.json, so
        # the kill happens on the default socket unless --socket overrides.
        res = tmuxio.tmux("kill-session", "-t", args.name,
                          socket=getattr(args, "socket", None))
        tmuxio.require(res, "session-missing")
        print(json.dumps({"ok": True, "session": args.name, "degraded": True},
                         ensure_ascii=False))
        return 0
    writer = records.V2Writer(state["session"])
    res = tmuxio.tmux("kill-session", "-t", state["session"], socket=state.get("socket"))
    if not tmuxio.ok(res):
        # classify, like every other tmux call: a missing socket is not a
        # missing session, and an agent told to "list what is there" while its
        # server is unreachable is chasing the wrong thing.
        code, hint = tmuxio.classify(res)
        raise TDError("session-missing" if code == "tmux-failed" else code,
                      res.stderr.strip() or f"session {state['session']!r} already gone",
                      hint=hint)
    writer.append("m", {"event": "stop"})
    state["stopped"] = True
    writer.state = state
    writer.save_state()
    print(json.dumps({"ok": True, "session": state["session"]}, ensure_ascii=False))
    return 0


def stamp_native_baseline(target, state: dict) -> None:
    """Record what tmux's own command status said *before* these bytes went in.

    `wait --cmd-done` compares against this to prove that the completion it saw
    belongs to this send. Stamped before injection (not after) because a fast
    command is finished by the time send-keys returns, and a baseline read late
    would already contain its answer. No-op on tmux 3.2a–3.7c, which has no
    such channel; the caller persists the state.
    """
    try:
        m = tmuxio.meta(target)
    except TDError:
        return
    if "pane_command_running" not in m:
        state.pop("native_baseline", None)  # no native channel: claim nothing
        return
    state["native_baseline"] = {
        "status": m.get("pane_command_status"),
        "end_time": m.get("pane_command_end_time"),
        "running": m.get("pane_command_running")}


def cmd_send(args) -> int:
    # Argument validation comes before any target resolution: an invalid
    # --repeat/--delay must fail as invalid-arguments regardless of whether
    # the session exists (error codes must not depend on target state).
    repeat = args.repeat
    delay = args.delay
    if repeat < 1:
        raise TDError("invalid-arguments", f"--repeat must be >= 1 (got {repeat})")
    if delay < 0:
        raise TDError("invalid-arguments", f"--delay must be >= 0 (got {delay})")
    target = resolve_target(args.name, getattr(args, "socket", None))
    state = load_state_or_none(target.session)
    # Records only for managed sessions: creating a V2Writer would mkdir a
    # state dir as a side effect — unmanaged panes (nested tmux) must not
    # accumulate bogus record dirs.
    writer = records.V2Writer(target.session) if state else None
    offset = writer.size() if writer else 0  # where output caused by this send starts
    if state is not None:
        stamp_native_baseline(target, state)
    sent_text = "".join(args.type or [])
    warning = None
    events: list[dict] = []
    for i in range(repeat):
        if i:
            time.sleep(delay)
        # Red line: every repetition is a full independent send (text and
        # keys stay in separate send-keys calls). Never collapse repeats
        # into one send-keys invocation — debounced menus would eat them.
        rep = tdinput.send(target, args.type or [], args.key or [], args.hex or [])
        if writer is not None:
            writer.append("i", rep)  # one i-event per repetition: trace shows the rhythm
        events += rep
    if writer is not None:
        state["last_send_offset"] = offset
        writer.state = state
        writer.save_state()
        if sent_text and tdinput.echo_broken(target.session, offset, sent_text):
            warning = "tty-echo-broken"
    # Say what went in, in the smallest readable form. The event list used to be
    # returned verbatim: it re-printed the typed text as JSON objects (three sets
    # of them under `--repeat 3`), which is bytes the caller already paid for and
    # cannot read back as one line — the one thing this reply is for.
    summary = {}
    if sent_text:
        summary["text"] = sent_text
    keys = [ev["key"] for ev in events if "key" in ev]
    if keys:
        summary["keys"] = keys
    hexes = [ev["hex"] for ev in events if "hex" in ev]
    if hexes:
        summary["hex"] = hexes
    summary["calls"] = len(events)
    result = {"ok": True, "offset": offset, "sent": summary}
    if repeat > 1:
        result["repeat"] = repeat
    if warning:
        result["warning"] = warning
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_fix_tty(args) -> int:
    target = resolve_target(args.name, getattr(args, "socket", None))
    state = records.read_state(target.session)
    writer = records.V2Writer(target.session)
    offset = writer.size()
    stamp_native_baseline(target, state)
    # C-u clears whatever half-typed line is sitting in the shell buffer,
    # so "stty sane" can never glue onto leftover input. Order matters:
    # clear first, then type the command, then Enter.
    events = tdinput.send(target, [], ["C-u"], [])
    events += tdinput.send(target, ["stty sane"], ["Enter"], [])
    writer.append("i", events)
    state["last_send_offset"] = offset
    writer.state = state
    writer.save_state()
    print(json.dumps({"ok": True, "fixed": "stty sane"}, ensure_ascii=False))
    return 0


def cmd_screen(args) -> int:
    target = resolve_target(args.name, getattr(args, "socket", None))
    if args.meta:
        # `--meta` is a different question (what is the pane doing) than the
        # text/grid flags (what is on the screen), and it used to swallow them
        # silently — an agent that asked for both got half an answer and no
        # signal that the other half was dropped. Say so instead.
        asked = [name for name, val in (
            ("-N", args.keep_trailing), ("--text", args.text), ("-J", args.join),
            ("--scrollback", args.scrollback), ("--grid", args.grid),
            ("--runs", args.runs), ("--grep", args.grep), ("--find", args.find),
            ("--element-at", args.element_at), ("--fg", args.fg),
            ("--bg", args.bg), ("--attr", args.attr)) if val]
        if asked:
            raise TDError(
                "invalid-arguments",
                f"--meta answers pane status only; it ignores {', '.join(asked)}",
                hint="two shapes, two calls: `screen -n NAME --meta` for cursor/"
                     "blank/stall/native command status, `screen -n NAME "
                     "[--grid|--runs|--grep|--find TEXT|--element-at X Y]` for "
                     "what is on the screen")
        state = load_state_or_none(target.session)
        m = tmuxio.meta(target)
        text = tmuxio.capture(target, ("-N", "-e"))
        stats = tdscreen.grid_stats(tdscreen.parse_grid(text))
        ctx = tdbudget.text_context(tdscreen.visible_text(text))
        m.update(session=target.session,
                 pane_id=state.get("pane_id") if state else None,
                 managed=state is not None,
                 blank=stats["blank"], cells=stats["painted_cells"],
                 nonblank_rows=stats["nonblank_rows"], context=ctx)
        if state is not None:
            # Stall diagnostics (tester feedback): "no new output" and "my
            # input never reached the program" look identical on screen.
            m["last_output_age_s"] = records.output_age_s(target.session)
            m["unexecuted_input"] = records.pending_input(
                target.session, state.get("last_send_offset", 0))
        else:
            m["last_output_age_s"] = None
            m["unexecuted_input"] = None
            m["degraded"] = "unmanaged"
        print(json.dumps(m, ensure_ascii=False))
        tdbudget.warn(ctx, target.session)
        return 0
    flags = []
    if args.keep_trailing or args.text:
        flags.append("-N")
    if args.join:
        flags.append("-J")
    if args.scrollback is not None:
        flags += ["-S", str(-args.scrollback)]
    if args.element_at is not None or args.grid or args.runs or args.grep \
            or args.find is not None:
        text = tmuxio.capture(target, tuple(flags + ["-e"]))
        grid = tdscreen.parse_grid(text)
        if args.element_at is not None:
            x, y = args.element_at
            out = json.dumps(tdscreen.cell_at(grid, x, y), ensure_ascii=False)
        elif args.grid:
            out = json.dumps(grid, ensure_ascii=False)
        elif args.runs:
            out = json.dumps(tdscreen.rows_to_runs(grid), ensure_ascii=False)
        elif args.grep:
            if args.fg is None and args.bg is None and not args.attr:
                raise TDError("invalid-arguments",
                              "--grep needs at least one of --fg/--bg/--attr")
            out = "".join(tdscreen.grid_row_text(row) + "\n"
                          for row in grid
                          if tdscreen.row_matches(row, args.fg, args.bg,
                                                  args.attr or ()))
        else:
            out = json.dumps(tdscreen.find_text(grid, args.find),
                             ensure_ascii=False)
        print(out, end="")
        tdbudget.warn(tdbudget.text_context(out), target.session)
        return 0
    text = tmuxio.capture(target, tuple(flags))
    # A silent all-whitespace answer is the failure mode the tester reported:
    # "is the pane empty, or did my read fail?" Say which it is (on stderr, so
    # piping stdout into grep stays exact).
    stats = tdscreen.text_stats(text)
    if stats["nonblank_lines"] == 0:
        print(f"note: pane shows no text ({stats['lines']} lines, none with "
              f"content) — this is an empty screen, not a failed read; run "
              f"`screen -n {target.session} --meta` for last_output_age_s / "
              f"unexecuted_input", file=sys.stderr)
    print(text, end="")
    tdbudget.warn(tdbudget.text_context(text), target.session)
    return 0


def require_shell_integration(target: tmuxio.Target, state: dict | None,
                             wait_s: float = 1.5) -> dict:
    """Shared gate for --cmd-done and --shell-ready.

    Distinguishes an unmanaged pane (exists in tmux, no state.json — e.g.
    nested tmux, a supported scenario) from a target that doesn't exist at
    all: the former gets `unmanaged-pane`, never a push to `start`.

    `wait_s` is how long the *stream* gets to prove it carries markers before
    the gate refuses: a shell that emits them on its own (fish, a user's own
    integration) is not behind on purpose, it may just be slow to paint its
    first prompt — under a loaded regression suite a fixed 1.5s sample failed
    one run in three. The caller's own timeout is the right budget, and the
    refusal still happens for a shell that truly never emits them.
    """
    if state is None:
        exists = tmuxio.tmux("has-session", "-t", target.session,
                             socket=target.socket).returncode == 0
        if exists:
            raise TDError(
                "unmanaged-pane",
                f"pane {target.to_arg()!r} is not managed by term-use "
                f"(no state.json)",
                hint="shell-protocol facts (--cmd-done/--shell-ready) need a "
                     "session started with: term-use start -n <name> --cmd "
                     "bash; the screen channels (--until/--quiet-ms) still "
                     "work here with degraded (heuristic) confidence. If this "
                     "IS a term-use session, XDG_STATE_HOME differs between "
                     "start and wait.")
        raise TDError(
            "session-missing",
            f"no tmux session {target.session!r} and no state.json for it",
            hint=f"expected state under {records.state_root()} — keep "
                 f"XDG_STATE_HOME consistent between start and wait, or "
                 f"start with: term-use start -n {target.session} --cmd bash")
    if not state.get("shell_integration"):
        # `start` can only record what it injected itself. A shell that emits
        # the markers on its own (fish >= 4.0) or carries another tool's
        # integration (VSCode's OSC 633, WezTerm's 133) is equally good
        # evidence — believe the stream, not the launch-time guess.
        if records.detect_shell_integration(target.session, wait_s=wait_s):
            state["shell_integration"] = True
            writer = records.V2Writer(target.session)
            writer.state = state
            writer.save_state()
        else:
            raise TDError(
                "no-shell-integration",
                f"session {target.session!r} emits no OSC 133 command markers",
                hint="start with --cmd bash or --cmd zsh to get the injected "
                     "integration (fish >= 4.0 emits the markers itself); "
                     "plain sh/dash has no pre-exec hook, so a done-marker is "
                     "not obtainable there — wait on the program's own output "
                     "with --until, or on pane death with --exit")
    return state


def _with_context(ctx, cond, **extra) -> dict:
    """Timeout evidence plus its price tag.

    The numbers ride inside the JSON rather than as a stderr warning: on the
    failure path stderr must stay single-line JSON, which is what every
    consumer of `{"error":…}` parses. `extra` carries the diagnosis facts
    (today `in_flight`), because an agent decides with them, not with the hint.
    """
    ev = waiting.timeout_evidence(ctx, cond)
    ev["context"] = tdbudget.text_context(ev.get("screen") or "")
    ev.update({k: v for k, v in extra.items() if v})
    return ev


def _exit_code_of(conditions: list) -> int | None:
    """The exit code the oracle that answered actually observed, if any.

    Quiet sampling has no exit code to report and --done must not invent one:
    None here is the honest answer, not a missing field.
    """
    for c in conditions:
        if isinstance(c, (waiting.CmdDone, waiting.ShellReady,
                          waiting.ExitCondition)):
            return getattr(c, "exit_code", None)
    return None


def cmd_wait(args) -> int:
    target = resolve_target(args.name, getattr(args, "socket", None))
    state = load_state_or_none(target.session)
    writer = records.V2Writer(target.session) if state else None

    def capture(scrollback: int) -> str:
        flags = ("-S", str(-scrollback)) if scrollback else ()
        return tmuxio.capture(target, flags)

    def pane_meta() -> dict:
        return tmuxio.meta(target)

    # Built before the conditions: --done's early "this channel cannot answer"
    # check wants screen evidence, and it runs while the oracle is being chosen.
    ctx = waiting.WaitContext(capture, pane_meta)
    conditions: list = []
    managed = state is not None
    via: str | None = None
    advice: str | None = None

    def inject_probe() -> None:
        """The `true` probe (the skill's standard loop, internalized): text
        and Enter are separate send-keys calls (the split-send red line)."""
        require_shell_integration(target, state, wait_s=min(args.timeout, 5.0))
        probe_offset = writer.size()
        stamp_native_baseline(target, state)
        probe = tdinput.send(target, ["true"], ["Enter"], [])
        writer.append("i", probe)
        state["last_send_offset"] = probe_offset
        writer.state = state
        writer.save_state()
        conditions.append(waiting.ShellReady(target.session))

    if args.since_send and args.until is None:
        raise TDError("invalid-arguments",
                      "--since-send scopes --until to output after the last "
                      "send, so it needs --until",
                      hint="or use --done to wait for the command itself")
    if args.since_send and args.scrollback:
        raise TDError("invalid-arguments",
                      "--since-send reads the raw stream since the last send "
                      "while --scrollback reads screen history — pick one")
    # Flags that used to be accepted and then quietly not used: a wait that
    # never reads the screen cannot be widened by --scrollback, and a probe
    # whose command is literally `true` cannot be expected to return 5.
    if args.scrollback and args.until is None:
        raise TDError("invalid-arguments",
                      f"--scrollback only widens the --until search; "
                      f"{'--done' if args.done else 'this condition'} never "
                      f"reads screen history",
                      hint="drop --scrollback, or add --until 'regex' "
                           "--scrollback N for the screen-shape question")
    if args.expect_code is not None and args.shell_ready:
        raise TDError("invalid-arguments",
                      "--shell-ready probes with `true`, which can only answer 0",
                      hint="to wait for a specific code, use --done "
                           "--expect-code C after sending the command")
    if args.until is not None:
        scope = "since-send" if args.since_send else "screen"
        if scope == "since-send" and not managed:
            raise TDError(
                "unmanaged-pane",
                f"--since-send needs term-use's own recording to know where "
                f"the last send started; pane {args.name!r} has no state.json",
                hint="address the fresh output some other way: --until matches "
                     "the current screen (and caps at heuristic on unmanaged "
                     "panes), or start the target with term-use start")
        conditions.append(waiting.UntilRegex(args.until, args.scrollback,
                                             target.session, managed=managed,
                                             scope=scope))
        advice = waiting.oracle_advice(state, "until")
    if args.done:
        # Strongest oracle this target actually supports — the agent should
        # not have to know which of our channels its pane can satisfy.
        dead = waiting.protocol_diagnosis(target.session, state, pane_meta(),
                                          (state or {}).get("last_send_offset", 0),
                                          ctx)
        if dead is not None and state is not None:
            # A passthrough foreground or a silent channel is knowable now;
            # burning the timeout on it teaches agents to distrust waits.
            raise dead
        oracle = waiting.choose_done_oracle(state)
        via = waiting.DONE_VIA[oracle]
        if oracle == "probe":
            if args.expect_code is not None:
                raise TDError(
                    "invalid-arguments",
                    f"--done fell back to the `true` probe (nothing has been "
                    f"sent yet), and a probe can only answer 0 — it cannot "
                    f"report code {args.expect_code}",
                    hint="send the command first, then --done --expect-code C; "
                         "waiting for a code from a command you never sent is "
                         "not a thing a wait can prove")
            inject_probe()
        elif oracle == "osc133":
            conditions.append(waiting.CmdDone(target.session, args.expect_code))
        elif oracle == "pane-exit":
            conditions.append(waiting.ExitCondition(args.expect_code, managed=True))
        else:
            conditions.append(waiting.QuietCondition(
                target.session, waiting.DEFAULT_DONE_QUIET_MS,
                managed=(oracle == "quiet")))
            advice = waiting.oracle_advice(state, oracle)
    if args.cmd_done:
        require_shell_integration(target, state, wait_s=min(args.timeout, 5.0))
        conditions.append(waiting.CmdDone(target.session, args.expect_code))
    if args.shell_ready:
        inject_probe()
    if args.exit:
        if not managed:
            # Anchor: fail fast on a target that never existed. tmux answers
            # a missing pane target with rc=0 and EMPTY format fields, so
            # probe for a real value; after this, an empty answer mid-wait
            # means the pane died (ExitCondition's collapse handling).
            if tmuxio.meta(target).get("pane_width") is None:
                raise TDError(
                    "session-missing",
                    f"no pane {args.name!r} (session exists but the pane "
                    f"target is empty, or the session is gone)",
                    hint="check the locator spelling [socket:]session:win.pane; "
                         "list candidates with: term-use sessions --socket "
                         "<socket>")
        conditions.append(waiting.ExitCondition(args.expect_code, managed=managed))
    if args.quiet_ms is not None:
        conditions.append(waiting.QuietCondition(target.session, args.quiet_ms,
                                                 managed=managed))
    if not conditions:
        raise TDError("invalid-arguments", "wait needs a condition",
                      hint="--done (finish the last thing you sent, strongest "
                           "oracle available), --cmd-done, --shell-ready, "
                           "--exit, --quiet-ms, or --until 'regex' "
                           "(add --since-send to match only new output)")
    cond = waiting.AllOf(conditions)

    deadline = time.monotonic() + args.timeout
    while True:
        if cond.evaluate(ctx):
            if writer is not None:
                writer.append("m", {"event": "wait-met",
                                    "confidence": cond.confidence})
            try:
                screen = capture(0)
            except TDError:
                screen = None  # pane/session just vanished (unmanaged --exit)
            verdict_ctx = tdbudget.text_context(screen or "")
            verdict = {
                "verdict": "met",
                "confidence": cond.confidence,
                **cond.to_json(True),
                "evidence": {"screen": screen},
                "context": verdict_ctx,
            }
            answered = next((getattr(c, "via", None) for c in conditions
                             if getattr(c, "via", None)), None)
            if any(getattr(c, "busy", False) for c in conditions):
                # The one misreading this tool can prevent: a quiet settle is a
                # statement about pixels, and an agent that hears "done" will
                # write a report about a program that is still running.
                verdict["note"] = (
                    "the pane stopped *repainting*, which is not the same as "
                    "finishing: a command reported its start and has not reported "
                    "a completion, so it is still running. For the completion "
                    "question use wait --done (or --exit when the program is the "
                    "pane)")
            if any(getattr(c, "silent_unproven", False) for c in conditions):
                verdict["note"] = (verdict.get("note", "") +
                                   " Nothing has been written since your send, "
                                   "so this settle rests on the screen having "
                                   "been still, not on any output.").strip()
            if answered:
                # Name the channel that actually answered: on tmux 3.8 the
                # native command status often beats our own byte scan to a
                # fast command's done marker, and claiming "osc133" while
                # reporting a code tmux handed us is the sort of detail that
                # makes people distrust confidence labels. An oracle that is
                # built *on top of* cmd-done (the probe) keeps its own name.
                verdict["channel"] = answered
                verdict["via"] = via if (via and via != "osc133") else answered
            elif via:
                verdict["via"] = via
            if advice:
                verdict["advice"] = advice
            if args.done:
                # The exit code belongs at the top level for --done: the whole
                # question an agent is asking is "did it finish, and how".
                verdict["exit_code"] = _exit_code_of(conditions)
                if verdict["exit_code"] is not None and managed:
                    # …and on a compound line it owes an explanation of *which
                    # part* of the line that number describes.
                    scope = waiting.line_scope(records.last_sent_text(
                        target.session, (state or {}).get("last_send_offset", 0)))
                    if scope:
                        verdict["note"] = (f"{verdict['note']} {scope}"
                                           if verdict.get("note") else scope)
            print(json.dumps(verdict, ensure_ascii=False))
            tdbudget.warn(verdict_ctx, target.session)
            return 0
        # EOF early-termination: a dead pane can never satisfy --until or
        # --cmd-done, so fail immediately instead of burning the timeout.
        if not args.exit and not any(
                isinstance(c, waiting.ExitCondition) for c in cond.conditions):
            if pane_meta().get("pane_dead"):
                raise waiting.pane_dead_error(ctx)
        if time.monotonic() >= deadline:
            break
        time.sleep(args.interval)
    dead = waiting.protocol_diagnosis(target.session, state, pane_meta(),
                                      (state or {}).get("last_send_offset", 0),
                                      ctx, after_timeout=True)
    if dead is not None and (args.cmd_done or args.shell_ready or args.done):
        raise dead
    if args.shell_ready and waiting.shell_ready_unmet(ctx, cond):
        # The probe timeout IS the signal: a live interactive program swallows
        # `true` instead of letting bash execute it. Surface it as its own
        # code so agents can branch (quit key + re-probe) without parsing text.
        # If the probe WAS answered, fall through to the plain timeout below:
        # the laggard is another condition (--until etc.), and pushing a quit
        # key at an idle shell would be a misdiagnosis.
        raise TDError("shell-not-ready",
                      f"shell did not answer the `true` probe within {args.timeout}s",
                      hint="likely inside an interactive program — inspect "
                           "evidence.screen, send the program's quit key, then "
                           "re-probe; if the shell was still starting, retry "
                           "with a longer --timeout",
                      evidence=_with_context(ctx, cond))
    busy = (managed and (args.done or args.cmd_done)
            and waiting.in_flight(target.session,
                                  (state or {}).get("last_send_offset", 0)))
    raise TDError("timeout",
                  f"conditions not met within {args.timeout}s",
                  hint=waiting.timeout_hint_for(cond, busy),
                  evidence=_with_context(ctx, cond, in_flight=busy))


def cmd_resize(args) -> int:
    target = resolve_target(args.name, getattr(args, "socket", None))
    res = tmuxio.tmux("resize-window", "-t", target.session,
                      "-x", str(args.width), "-y", str(args.height),
                      socket=target.socket)
    tmuxio.require(res, "session-missing")
    state = load_state_or_none(target.session)
    if state is not None:
        # Recorded, because a resize reflows the screen: `replay` has to
        # reproduce it in order to rebuild any frame after it faithfully.
        records.V2Writer(target.session).append(
            "r", {"cols": args.width, "rows": args.height})
    print(json.dumps({"ok": True, "width": args.width, "height": args.height},
                     ensure_ascii=False))
    return 0


def cmd_out(args) -> int:
    """What the pane printed since the last thing you sent, as text.

    The highest-frequency thing an agent does is "my command finished, now
    read it" — and `screen` answers with the whole visible screen plus a
    scrollback it has to grep by hand. This is the scoped, budgeted version.
    """
    target = resolve_target(args.name, getattr(args, "socket", None))
    state = load_state_or_none(target.session)
    if state is None:
        raise TDError(
            "unmanaged-pane",
            f"out reads term-use's own recording of the pane's output; "
            f"{args.name!r} has no state.json",
            hint="use `screen -n NAME` for the visible text of an unmanaged "
                 "pane, or start the target with term-use start to get the "
                 "recorded stream")
    offset = 0 if args.all else int(state.get("last_send_offset", 0))
    raw = records.stream_since(target.session, offset)
    text = replay.linear(raw)
    shown, omitted = replay.trim(text, args.max_bytes)
    ctx = tdbudget.text_context(shown,
                                extra={"full_bytes": len(text.encode("utf-8"))})
    redrawn = replay.looks_redrawn(raw)
    if args.json:
        print(json.dumps({"ok": True, "scope": "all" if args.all
                         else "since-send",
                          "bytes": ctx["bytes"], "truncated": omitted > 0,
                          "omitted_bytes": omitted, "redrawn": redrawn,
                          "text": shown, "context": ctx}, ensure_ascii=False))
    else:
        sys.stdout.write(shown if shown.endswith("\n") else shown + "\n")
        if omitted:
            print(f"note: {omitted} interior bytes omitted from the middle "
                  f"(head and tail kept); rerun with --max-bytes 0 for all of it",
                  file=sys.stderr)
        if redrawn:
            print("note: this program repositions text with cursor escapes "
                  "(alt-screen/TUI style), so this linear text is not the frame "
                  "it showed — get the real frame with: term-use replay -n "
                  f"{target.session} --at last-send", file=sys.stderr)
    tdbudget.warn(ctx, target.session)
    return 0


def cmd_replay(args) -> int:
    """The screen at an earlier moment, rebuilt through tmux itself."""
    if args.o and args.format != "png":
        raise TDError(
            "invalid-arguments",
            f"-o writes a file, and only --format png is a file; "
            f"--format {args.format} writes to stdout",
            hint="drop -o to read the text/json on stdout, or ask for "
                 "--format png -o frame.png")
    if args.format == "png" and os.environ.get("TERM_USE_DISABLE_IMAGE") == "1":
        # Same denial as `screenshot`: the heuristics-only blind-test mode must
        # not have a second door into the image channel.
        raise TDError("pillow-missing",
                      "image channel disabled by TERM_USE_DISABLE_IMAGE=1",
                      hint="this session runs heuristics-only; use the text channels")
    target = resolve_target(args.name, getattr(args, "socket", None))
    head = records.header(target.session)
    if head is None:
        raise TDError(
            "session-missing",
            f"no raw.log recording to replay for {args.name!r}",
            hint="replay works on sessions started by term-use start; the "
                 "current screen of any pane is `screen -n NAME`")
    _h, elapsed, offset = replay.resolve_at(target.session, args.at)
    cols = int(head.get("width") or 80)
    rows = int(head.get("height") or 24)
    text, frame = replay.frame(target.session, elapsed=elapsed, offset=offset,
                               cols=cols, rows=rows, settle_s=args.timeout)
    # The replayed pane is the authority on its own geometry: a recorded resize
    # may have moved it away from the header the session started with.
    cols = frame.get("pane_width") or cols
    rows = frame.get("pane_height") or rows
    cursor = None
    if args.cursor and isinstance(frame.get("cursor_x"), int) \
            and isinstance(frame.get("cursor_y"), int):
        cursor = (frame["cursor_x"], frame["cursor_y"])
    grid = tdscreen.parse_grid(text)
    stats = tdscreen.grid_stats(grid)
    plain = tdscreen.visible_text(text)
    if args.format == "png":
        out_path = args.o or default_shot_path(target.session, "png",
                                               load_state_or_none(target.session) is not None)
        try:
            info = tdscreenshot.render(grid, out_path, cols=cols,
                                       rows=rows, cursor=cursor)
        except ImportError:
            raise TDError("pillow-missing",
                          "Pillow is required for the image channel",
                          hint="pkg install python-pillow (or: pip install Pillow)")
        if info["font_source"] == "bundled" and os.environ.get("TERM_USE_FONT_DIRS"):
            print("note: no monospace font was found in TERM_USE_FONT_DIRS, so the "
                  "face bundled with term-use drew this picture — install a system "
                  "font (or fix the dir) for the machine's own look", file=sys.stderr)
        ctx = tdbudget.image_context(info["path"], info["size"])
        print(json.dumps({"ok": True, "at": args.at, "format": "png", **info,
                          "cursor": list(cursor) if cursor else None,
                          "context": ctx}, ensure_ascii=False))
        tdbudget.warn(ctx, target.session)
        return 0
    if args.format == "json":
        ctx = tdbudget.text_context(plain)
        print(json.dumps({"ok": True, "at": args.at, "grid": [cols, rows],
                          "blank": stats["blank"],
                          "cells": stats["painted_cells"], "text": plain,
                          "cursor": list(cursor) if cursor else None,
                          "alternate_on": frame.get("alternate_on"),
                          "context": ctx}, ensure_ascii=False))
        tdbudget.warn(ctx, target.session)
        return 0
    sys.stdout.write(plain if plain.endswith("\n") else plain + "\n")
    tdbudget.warn(tdbudget.text_context(plain), target.session)
    return 0


def cmd_mouse_detect(args) -> int:
    target = resolve_target(args.name, getattr(args, "socket", None))
    tail = records.stream_since(target.session, 0)[-16384:]
    print(json.dumps(tdinput.mouse_mode(tail), ensure_ascii=False))
    return 0


def cmd_click(args) -> int:
    target = resolve_target(args.name, getattr(args, "socket", None))
    tail = records.stream_since(target.session, 0)[-16384:]
    mode = tdinput.mouse_mode(tail)
    if not mode["enabled"]:
        raise TDError("mouse-not-enabled",
                      "the program has not enabled mouse reporting",
                      hint="only programs that turned on \\e[?1000/1002/1003h accept clicks")
    tdinput.click(target, args.x, args.y)
    print(json.dumps({"ok": True, "clicked": [args.x, args.y],
                      "sgr": mode["sgr"]}, ensure_ascii=False))
    return 0


def default_shot_path(session: str, fmt: str, managed: bool) -> str:
    """Where a screenshot goes when -o is omitted.

    Managed sessions keep their pictures beside their evidence; unmanaged
    targets must not grow a record directory as a side effect of being looked
    at, so those land in the temp dir. Numbered, never overwritten: two
    captures of the same pane are how an agent compares frames.
    """
    d = records.session_dir(session) if managed else Path(tempfile.gettempdir())
    if managed:
        d.mkdir(parents=True, exist_ok=True)
    n = 1
    path = d / f"screenshot-{n}.{fmt}"
    while path.exists():
        n += 1
        path = d / f"screenshot-{n}.{fmt}"
    return str(path)


def cmd_screenshot(args) -> int:
    if os.environ.get("TERM_USE_DISABLE_IMAGE") == "1":
        # Blind-test switch: heuristics-only testers get the image channel
        # denied with the same structured error shape as a missing Pillow.
        raise TDError("pillow-missing",
                      "image channel disabled by TERM_USE_DISABLE_IMAGE=1",
                      hint="this session runs heuristics-only; use the text channels")
    target = resolve_target(args.name, getattr(args, "socket", None))
    state = load_state_or_none(target.session)
    out_path = args.o or default_shot_path(target.session, "png",
                                           state is not None)
    if not out_path.lower().endswith((".png", ".PNG")):
        # With one output format there is still a way to lie: writing PNG bytes
        # into a name that says .jpg. Refuse rather than ship a mislabeled file.
        raise TDError("invalid-arguments",
                      f"screenshot writes PNG only; {out_path!r} promises another "
                      f"format", hint="choose a .png path, e.g. "
                                      "screenshot -n NAME -o shot.png")
    # Geometry comes from tmux, not from the capture: a screen that painted
    # nothing has no content bounds, and cropping to nothing used to crash
    # the renderer. -N keeps trailing cells so a painted background survives
    # to the picture (tmux strips them by default).
    pane = tmuxio.meta(target)
    cols = pane.get("pane_width") or (state or {}).get("width")
    rows = pane.get("pane_height") or (state or {}).get("height")
    cursor = None
    if args.cursor and isinstance(pane.get("cursor_x"), int) \
            and isinstance(pane.get("cursor_y"), int):
        # Where the pane says the cursor is: "which row am I on" is a layout
        # question, and an outline answers it without hiding the glyph.
        cursor = (pane["cursor_x"], pane["cursor_y"])
    grid = tdscreen.parse_grid(tmuxio.capture(target, ("-N", "-e")))
    try:
        info = tdscreenshot.render(grid, out_path, cols=cols,
                                   rows=rows, cursor=cursor,
                                   font_size=args.font_size)
    except ImportError:
        raise TDError("pillow-missing",
                      "Pillow is required for the image channel",
                      hint="pkg install python-pillow (or: pip install Pillow)")
    if info["font_source"] == "bundled" and os.environ.get("TERM_USE_FONT_DIRS"):
        print("note: no monospace font was found in TERM_USE_FONT_DIRS, so the "
              "face bundled with term-use drew this picture — install a system "
              "font (or fix the dir) for the machine's own look", file=sys.stderr)
    ctx = tdbudget.image_context(info["path"], info["size"])
    print(json.dumps({"ok": True, **info, "context": ctx}, ensure_ascii=False))
    tdbudget.warn(ctx, target.session, channel="image")
    return 0


def cmd_clear(args) -> int:
    """Reclaim the pane's screen and scrollback — the way out of a fat capture.

    An agent that just paid 4k tokens for a screen dump pays it again on every
    later read, because `capture-pane` shows whatever is on the screen and its
    history. This is the one command whose whole purpose is making the *next*
    capture cheaper.
    """
    target = resolve_target(args.name, getattr(args, "socket", None))
    state = load_state_or_none(target.session)
    writer = records.V2Writer(target.session) if state else None

    def rows_visible() -> int:
        grid = tdscreen.parse_grid(tmuxio.capture(target, ("-N", "-e")))
        return tdscreen.grid_stats(grid)["nonblank_rows"]

    before = rows_visible()
    did: list[str] = []
    if not args.history_only:
        # C-l is the keystroke a shell/readline already binds to clear-display:
        # it repaints the prompt without executing anything, unlike typing
        # `clear` + Enter (which would run a command and pollute history).
        events = tdinput.send(target, [], ["C-l"], [])
        if writer is not None:
            writer.append("i", events)
        did.append("C-l")
    tmuxio.require(tmuxio.tmux("clear-history", "-t", target.to_arg(),
                               socket=target.socket), "session-missing")
    did.append("clear-history")
    after = rows_visible()
    result = {"ok": True, "cleared": did, "rows_before": before, "rows_after": after,
              "history_size": tmuxio.meta(target).get("history_size")}
    if after >= before and not args.history_only:
        result["note"] = ("the screen still holds its content — a full-screen "
                          "program repaints on its own or does not bind C-l; "
                          "scrollback is gone. Pass --history-only to avoid "
                          "touching the program's input at all.")
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_trace(args) -> int:
    path = records.session_dir(args.name) / "raw.log"
    if not path.exists():
        raise TDError("session-missing",
                      f"no raw.log for session {args.name!r}",
                      hint="run: term-use start -n <name> --cmd <command>")
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            ev = json.loads(line)
            if args.format == "json":
                print(json.dumps(ev, ensure_ascii=False))
                continue
            if isinstance(ev, dict):  # v2 header
                # `started=` carries the recording's own epoch so every
                # `[elapsed]` below can be turned into wall-clock without
                # reading raw.log by hand. Attributing "which invocation wrote
                # this file" is otherwise guesswork against an mtime.
                start = ev.get("timestamp")
                if start:
                    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start))
                    extra = f" started={stamp} ({start!r})"
                else:
                    extra = ""
                print(f"# v{ev.get('version')} {ev.get('width')}x{ev.get('height')}"
                      f" cmd={ev.get('cmd')!r}{extra}")
                continue
            ts, code, data = ev
            if code == "o":
                preview = data.replace("\n", "\\n")[:48]
                print(f"[{ts:9.3f}] o {len(data):5d}B {preview!r}")
            elif code == "i":
                parts = []
                for e in data:
                    if "text" in e:
                        parts.append(f"type={e['text']!r}")
                    elif "key" in e:
                        parts.append(f"key={e['key']}")
                    elif "hex" in e:
                        parts.append(f"hex={e['hex']}")
                print(f"[{ts:9.3f}] i {' '.join(parts)}")
            elif code == "m":
                name = data.get("event") if isinstance(data, dict) else None
                print(f"[{ts:9.3f}] m {name or json.dumps(data, ensure_ascii=False)}")
            else:
                print(f"[{ts:9.3f}] {code} {data!r}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="term-use",
        description="Drive and debug interactive terminal programs via tmux (v2).",
    )
    # `--version` because agents probe it like any other CLI, and without it
    # argparse answered "the following arguments are required: COMMAND" — a
    # misleading error for a tool whose whole contract is honest reporting.
    parser.add_argument("--version", action="version",
                        version=f"term-use {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("start", help="create a detached tmux session with v2 recording")
    p.add_argument("-n", "--name", required=True, help="session name")
    p.add_argument("--cmd", default="bash", help="command to run (default: bash)")
    p.add_argument("--width", type=int, default=120)
    p.add_argument("--height", type=int, default=40)
    p.add_argument("--socket", default=None, help="tmux socket name (-L)")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("stop", help="kill the session and finalize raw.log")
    p.add_argument("-n", "--name", required=True, help="session name (or target)")
    p.add_argument("--socket", default=None, help="tmux socket name (-L) override")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("send", help="inject text/keys (always split into separate calls)")
    p.add_argument("-n", "--name", required=True, help="session name (or target)")
    p.add_argument("--socket", default=None, help="tmux socket name (-L) override")
    p.add_argument("--type", action="append", metavar="TEXT",
                   help="literal text to type (repeatable, sent before keys)")
    p.add_argument("--key", action="append", metavar="KEY",
                   help="key name e.g. Enter Escape C-c (repeatable)")
    p.add_argument("--hex", action="append", metavar="HEX",
                   help="raw byte via send-keys -H e.g. 1c for C-\\ (repeatable)")
    p.add_argument("--repeat", type=int, default=1, metavar="N",
                   help="repeat the whole injection N times, each repetition a "
                        "full independent send (debounced menus, double-key "
                        "rhythms like Escape Escape / double C-c)")
    p.add_argument("--delay", type=float, default=0.35, metavar="S",
                   help="seconds between repetitions (default 0.35 — the gap "
                        "measured to clear real menu debouncing)")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("wait", help="poll the screen until a regex matches")
    p.add_argument("-n", "--name", required=True, help="session name (or target)")
    p.add_argument("--socket", default=None, help="tmux socket name (-L) override")
    p.add_argument("--until", default=None, help="regex to wait for")
    p.add_argument("--done", action="store_true",
                   help="wait for the last thing you sent to finish, using the "
                        "strongest oracle this target supports (verdict reports "
                        "`via` + exit_code: osc133/shell-ready-probe/pane-exit "
                        "are fact, quiet-sampling is heuristic)")
    p.add_argument("--since-send", action="store_true",
                   help="with --until: match only output the pane emitted after "
                        "the last send, so a stale anchor on screen cannot "
                        "satisfy the wait (needs a managed session)")
    p.add_argument("--cmd-done", action="store_true",
                   help="wait for the OSC 133 command-done marker (fact)")
    p.add_argument("--shell-ready", action="store_true",
                   help="send a `true` probe and wait until the shell answers "
                        "(fact); use right after start and to confirm the shell "
                        "took input back after a nested program — timeout means "
                        "the shell is NOT ready (the probe was swallowed)")
    p.add_argument("--exit", action="store_true",
                   help="wait for the pane to exit (remain-on-exit, fact)")
    p.add_argument("--quiet-ms", type=int, default=None, metavar="N",
                   help="stable for N ms via double sampling (heuristic)")
    p.add_argument("--expect-code", type=int, default=None, metavar="N",
                   help="with --cmd-done/--exit: fail immediately unless exit code == N")
    p.add_argument("--scrollback", type=int, default=0, metavar="N",
                   help="search N lines of history too (default: visible screen)")
    p.add_argument("--timeout", type=float, default=10.0, help="max seconds (default 10)")
    p.add_argument("--interval", type=float, default=0.1, help="poll interval (default 0.1)")
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("screen", help="observe the pane: text, meta, SGR grid/runs")
    p.add_argument("-n", "--name", required=True, help="session name (or target)")
    p.add_argument("--socket", default=None, help="tmux socket name (-L) override")
    p.add_argument("--meta", action="store_true", help="pane status JSON (cursor/modes/history)")
    p.add_argument("-N", "--keep-trailing", action="store_true", help="keep trailing spaces")
    p.add_argument("--text", action="store_true",
                   help="plain text, the default reading — accepted so "
                        "`screen --text` is a valid guess, not an argparse error")
    p.add_argument("-J", "--join", action="store_true", help="join wrapped lines into logical lines")
    p.add_argument("--scrollback", type=int, metavar="N", help="include N lines of history")
    p.add_argument("--grid", action="store_true", help="SGR cell grid JSON")
    p.add_argument("--runs", action="store_true", help="attribute run view JSON")
    p.add_argument("--grep", action="store_true", help="print rows matching --fg/--bg/--attr")
    p.add_argument("--find", default=None, metavar="TEXT",
                   help="literal text search; JSON matches with 1-based "
                        "click-ready coords (feed straight into click)")
    p.add_argument("--fg", default=None, metavar="COLOR")
    p.add_argument("--bg", default=None, metavar="COLOR")
    p.add_argument("--attr", action="append", metavar="A", help="e.g. bold, reverse (repeatable)")
    p.add_argument("--element-at", nargs=2, type=int, metavar=("X", "Y"), help="single cell JSON")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("mouse-detect", help="report the pane's mouse tracking mode")
    p.add_argument("-n", "--name", required=True, help="session name (or target)")
    p.add_argument("--socket", default=None, help="tmux socket name (-L) override")
    p.set_defaults(func=cmd_mouse_detect)

    p = sub.add_parser("click", help="inject an SGR mouse click (1-based coords)")
    p.add_argument("-n", "--name", required=True, help="session name (or target)")
    p.add_argument("--socket", default=None, help="tmux socket name (-L) override")
    p.add_argument("x", type=int, help="column, 1-based")
    p.add_argument("y", type=int, help="row, 1-based")
    p.set_defaults(func=cmd_click)

    p = sub.add_parser("fix-tty", help="restore sane terminal settings (stty sane)")
    p.add_argument("-n", "--name", required=True, help="session name (or target)")
    p.add_argument("--socket", default=None, help="tmux socket name (-L) override")
    p.set_defaults(func=cmd_fix_tty)

    p = sub.add_parser("screenshot", help="render the pane to a png (Pillow)")
    p.add_argument("-n", "--name", required=True, help="session name (or target)")
    p.add_argument("--socket", default=None, help="tmux socket name (-L) override")
    p.add_argument("-o", "--out", dest="o", default=None, metavar="PATH",
                   help="output file (default: numbered file beside the "
                        "session's evidence; never overwrites an earlier shot)")
    p.add_argument("--font-size", type=int, default=16, metavar="PX",
                   help="glyph size in px — bigger is easier to read but the "
                        "image costs more context (default 16)")
    p.add_argument("--no-cursor", dest="cursor", action="store_false",
                   help="omit the cursor outline (default: outline it, so "
                        "'which row am I on' is answerable from the picture)")
    p.set_defaults(cursor=True)
    p.set_defaults(func=cmd_screenshot)

    p = sub.add_parser("clear", help="reclaim the pane's screen + scrollback "
                                     "(keeps later captures cheap)")
    p.add_argument("-n", "--name", required=True, help="session name (or target)")
    p.add_argument("--socket", default=None, help="tmux socket name (-L) override")
    p.add_argument("--history-only", action="store_true",
                   help="drop only the scrollback; inject no keystroke at all")
    p.set_defaults(func=cmd_clear)

    p = sub.add_parser("resize", help="resize the session's window (forces redraw)")
    p.add_argument("-n", "--name", required=True, help="session name (or target)")
    p.add_argument("--width", type=int, required=True)
    p.add_argument("--height", type=int, required=True)
    p.add_argument("--socket", default=None, help="tmux socket name (-L) override")
    p.set_defaults(func=cmd_resize)

    p = sub.add_parser("sessions", help="list tmux sessions on a socket")
    p.add_argument("--socket", default=None, help="tmux socket name (-L); default socket if omitted")
    # `-n` everywhere else means "which session"; here it means "filter to one".
    # Refusing it with raw argparse text breaks the one-promise-per-failure rule
    # for the obvious question ("is my session there?").
    p.add_argument("-n", "--name", default=None, metavar="SESSION",
                   help="list only this session (empty array if it is not there)")
    p.set_defaults(func=cmd_sessions)

    p = sub.add_parser("trace", help="project raw.log (human summary or raw NDJSON)")
    p.add_argument("-n", "--name", required=True, help="session name")
    p.add_argument("--socket", default=None,
                   help="accepted for target uniformity; trace reads local records")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.set_defaults(func=cmd_trace)

    p = sub.add_parser("out", help="what the pane printed since your last send "
                                   "(text, inside a context budget)")
    p.add_argument("-n", "--name", required=True, help="session name")
    p.add_argument("--socket", default=None,
                   help="accepted for target uniformity; out reads local records")
    p.add_argument("--all", action="store_true",
                   help="the whole session instead of since the last send")
    p.add_argument("--max-bytes", type=int, default=10000, metavar="N",
                   help="byte budget: keep head + tail, mark what was omitted "
                        "(0 = everything, default 10000)")
    p.add_argument("--json", action="store_true",
                   help="structured result (text, truncated, omitted_bytes, context)")
    p.set_defaults(func=cmd_out)

    p = sub.add_parser("replay", help="the screen at an earlier moment, rebuilt "
                                      "by feeding raw.log back through tmux")
    p.add_argument("-n", "--name", required=True, help="session name")
    p.add_argument("--socket", default=None,
                   help="accepted for target uniformity; replay reads local records")
    p.add_argument("--at", required=True, metavar="WHEN",
                   help="elapsed seconds from session start, offset:N, start, "
                        "or last-send")
    p.add_argument("--format", choices=["text", "json", "png"], default="text")
    p.add_argument("-o", "--out", dest="o", default=None, metavar="PATH",
                   help="output file for --format png")
    p.add_argument("--timeout", type=float, default=10.0,
                   help="how long to wait for the scratch pane to consume the "
                        "recording (default 10)")
    p.add_argument("--no-cursor", dest="cursor", action="store_false",
                   help="omit the cursor outline from --format png (default: "
                        "draw where the replayed pane left its cursor)")
    p.set_defaults(cursor=True, func=cmd_replay)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except TDError as e:
        print(e.to_json(), file=sys.stderr)
        return e.exit_code
