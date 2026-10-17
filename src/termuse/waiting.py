"""Wait engine: pluggable conditions, AND composition, verdict JSON.

Every condition carries a confidence label (design section 3):
  fact       — ground truth from the terminal protocol (OSC 133, pane death)
  inference  — screen-text regex (echo can fake it)
  heuristic  — sampling-based stability (quiet detection)
The composite verdict takes the *weakest* confidence of its conditions.
Timeout never kills the target; it returns an evidence snapshot.
"""
import re
import time

from termuse import osc133, records
from termuse import screen as screen_mod
from termuse.errors import TDError

CONFIDENCE_ORDER = ("fact", "inference", "heuristic")  # strong -> weak

# What `wait --done` settles on when the target supports no stronger oracle.
DEFAULT_DONE_QUIET_MS = 700


class Condition:
    label: str = "condition"
    confidence: str = "inference"
    # Degradation marker: why this condition runs with reduced evidence
    # (e.g. "unmanaged" — no state.json/raw.log, so part of the machinery
    # cannot run). None on fully-equipped waits.
    degraded: str | None = None

    def evaluate(self, ctx) -> bool:
        raise NotImplementedError

    def to_json(self, met: bool) -> dict:
        j = {"type": self.label, "confidence": self.confidence, "met": met}
        if self.degraded:
            j["degraded"] = self.degraded
        return j


class WaitContext:
    """What conditions may observe: screen text (+history) and pane meta."""

    def __init__(self, capture, meta):
        self.capture = capture  # callable(scrollback: int) -> str
        self.meta = meta        # callable() -> dict


class UntilRegex(Condition):
    label = "until"
    confidence = "inference"

    def __init__(self, pattern: str, scrollback: int = 0, session: str | None = None,
                 managed: bool = True, scope: str = "screen"):
        self.pattern = pattern
        self.scrollback = scrollback
        self.session = session
        self.managed = managed
        self.scope = scope
        self.echo_suspect = False
        self.rx = re.compile(pattern, re.DOTALL | re.MULTILINE)
        if not managed:
            # Unmanaged pane (nested tmux): no i-events exist, so echo-suspect
            # detection cannot run and our own echo can fake the match. Cap
            # the claim and say why (nested tmux is a supported scenario —
            # degrade, don't refuse).
            self.confidence = "heuristic"
            self.degraded = "unmanaged"

    def evaluate(self, ctx) -> bool:
        if self.scope == "since-send":
            return self._evaluate_stream()
        screen = ctx.capture(self.scrollback)
        if self.rx.search(screen) or self._match_stripped(screen):
            return self._check_echo(screen)
        return False

    def _evaluate_stream(self) -> bool:
        """Match only what the pane emitted *after* the last send.

        `--until` reads the screen, so an anchor still sitting on it from an
        earlier command satisfies a new wait instantly — the skill's stale-
        anchor warning, now escapable. The raw stream since `last_send_offset`
        cannot contain anything from before the send, so a match here is
        genuinely new output. Caveat worth knowing: this is the *byte* stream,
        which a line-oriented program writes linearly but a full-screen redraw
        can split with cursor-motion sequences — for TUI frames, keep matching
        the screen.
        """
        state = records.read_state(self.session)
        stream = records.stream_since(self.session, state.get("last_send_offset", 0))
        text = screen_mod.visible_text(stream)
        if self.rx.search(text) or self._match_stripped(text):
            return self._check_echo(text)
        return False

    def _match_stripped(self, screen: str) -> bool:
        # Self-heal red line: the terminal strips trailing spaces ('name: '
        # prints as 'name:'), so retry against per-line-rstripped text, and
        # against rstripped text + a sentinel space (pattern requires one).
        stripped = "\n".join(line.rstrip() for line in screen.splitlines())
        if self.rx.search(stripped):
            return True
        sentinel = "\n".join(line.rstrip() + " " for line in screen.splitlines())
        return bool(self.rx.search(sentinel))

    def _check_echo(self, screen: str) -> bool:
        """Echo red line: if the matched text is just what we last typed, the
        match is our own echo — downgrade confidence and flag it."""
        self.echo_suspect = False
        if self.session is None:
            return True
        m = self.rx.search(screen)
        if not m or not m.group(0):
            return True
        try:
            for _ts, code, data, _pos in records.events_since(self.session, 0):
                if code != "i" or not isinstance(data, list):
                    continue
                typed = "".join(e.get("text", "") for e in data
                                if isinstance(e, dict))
                if typed and m.group(0) in typed:
                    self.echo_suspect = True
                    self.confidence = "heuristic"
                    return True
        except (TDError, OSError):
            # Managed on paper, but records vanished: echo can't be checked.
            self.confidence = "heuristic"
            self.degraded = "records-unavailable"
        return True

    def to_json(self, met: bool) -> dict:
        return {**super().to_json(met), "pattern": self.pattern,
                "scrollback": self.scrollback, "scope": self.scope,
                "echo_suspect": self.echo_suspect}


class AllOf(Condition):
    """AND composition; confidence degrades to the weakest member.

    Recomputed after every evaluate: a condition may downgrade itself at
    runtime (e.g. until matching our own echo)."""

    def __init__(self, conditions: list[Condition]):
        if not conditions:
            raise ValueError("AllOf needs at least one condition")
        self.conditions = conditions
        self.label = "all-of"
        self._recompute()

    def _recompute(self) -> None:
        weakest = max(CONFIDENCE_ORDER.index(c.confidence) for c in self.conditions)
        self.confidence = CONFIDENCE_ORDER[weakest]

    def evaluate(self, ctx) -> bool:
        result = all(c.evaluate(ctx) for c in self.conditions)
        self._recompute()
        return result

    def to_json(self, met: bool) -> dict:
        return {**super().to_json(met),
                "conditions": [c.to_json(met) for c in self.conditions]}


class CmdDone(Condition):
    """OSC 133 D marker after a C, scanned from raw.log since the last send.

    fact confidence: the exit code comes from the shell protocol, not from
    screen text. Expect-code mismatch fails immediately (never waits out the
    timeout). Requires shell integration recorded in state.json.
    """
    label = "cmd-done"
    confidence = "fact"

    def __init__(self, session: str, expect_code: int | None = None):
        self.session = session
        self.expect_code = expect_code
        self.exit_code: int | None = None

    def _stream(self) -> str:
        state = records.read_state(self.session)
        return records.stream_since(self.session, state.get("last_send_offset", 0))

    def evaluate(self, ctx) -> bool:
        """Replay the full o-stream through the state machine, then accept
        the newest done/aborted event only if it was emitted after the last
        send (byte position >= last_send_offset). Full replay keeps C-marker
        continuity: the C of a long-running command may sit far behind the
        scan start."""
        state = records.read_state(self.session)
        offset = state.get("last_send_offset", 0)
        sc = osc133.OSC133Scanner()
        last_done = None    # (code, byte_pos) — a D preceded by a C
        last_abort = None   # byte_pos of a stray D with no C
        for _ts, code, data, pos in records.events_since(self.session, 0):
            if code != "o" or not isinstance(data, str):
                continue
            for ev in sc.feed(data):
                if ev["kind"] == "done":
                    last_done = (ev.get("code"), pos)
                elif ev["kind"] == "aborted":
                    last_abort = pos
        # A done is never masked by a later stray marker: shells emit more than
        # one D per prompt (an integration hook plus the user's own), and the
        # trailing one carries no command.
        if last_done is None or last_done[1] < offset:
            return False
        code_ = last_done[0]
        del last_abort  # informative only; completion is already proven
        self.exit_code = code_
        if self.expect_code is not None and self.exit_code != self.expect_code:
            raise osc133.mismatch_error(
                self.expect_code, self.exit_code,
                {"exit_code": self.exit_code, "expected": self.expect_code,
                 "screen": ctx.capture(0)})
        return True

    def to_json(self, met: bool) -> dict:
        return {**super().to_json(met), "expect_code": self.expect_code,
                "exit_code": self.exit_code if met else None}


class ShellReady(Condition):
    """The shell answers a `true` probe (fact).

    Internalizes the skill's standard probe loop: the client sends `true`
    + Enter, then requires a cmd-done (exit 0) emitted after the probe.
    Works where a bare --cmd-done cannot:
      - right after `start` (bash's startup D has no preceding C and is
        classified aborted, so --cmd-done would always time out);
      - after leaving a nested program (a live REPL swallows the probe,
        so the wait keeps failing — the timeout IS the "still inside" signal,
        surfaced as the shell-not-ready error code).
    The probe itself is injected by the CLI (cmd_wait) so last_send_offset
    stays authoritative; this condition only does the fact check.
    """

    label = "shell-ready"
    confidence = "fact"

    def __init__(self, session: str):
        self.session = session
        self._done = CmdDone(session, 0)

    def evaluate(self, ctx) -> bool:
        return self._done.evaluate(ctx)

    def to_json(self, met: bool) -> dict:
        return {**self._done.to_json(met), "type": self.label}

    @property
    def exit_code(self) -> int | None:
        return self._done.exit_code


SHELL_COMMANDS = ("bash", "sh", "dash", "zsh", "ksh", "fish", "csh", "tcsh",
                  "login", "shell")
# Flags that consume the token after them, so a script operand is not mistaken
# for one of their values.
_SHELL_FLAGS_WITH_ARG = ("--rcfile", "--init-file", "-O", "--optimize-environment")


def shell_owns_the_pane(cmd: str | None) -> bool:
    """True for an interactive shell invocation (it outlives every command).

    `bash` / `zsh -i` stay alive forever, so pane death says nothing about
    whether a command finished. `bash script.sh` and `sh -c '...'` are the
    program itself: they exit when the work does, so pane death IS the fact.
    """
    words = str(cmd or "").split()
    if not words:
        return False
    base = words[0].rsplit("/", 1)[-1]
    if not any(base == s or base.startswith(s + "-") for s in SHELL_COMMANDS):
        return False
    if "-c" in words or "-ic" in words or any(w.startswith("-c") and len(w) > 2
                                              for w in words[1:]):
        return False  # ran a command string, then exits
    rest = words[1:]
    i = 0
    while i < len(rest):
        w = rest[i]
        if w in _SHELL_FLAGS_WITH_ARG:
            i += 2
            continue
        if w.startswith("-"):
            i += 1
            continue
        return False  # a bare operand: a script file
    return True


def program_is_pane(cmd: str | None) -> bool:
    """True when the pane's own process is the target program (not a shell).

    For those, "the command finished" and "the pane died" are the same event,
    so `wait --exit` is the fact-level oracle; in a shell pane the pane never
    dies and completion has to come from the shell protocol.
    """
    words = str(cmd or "").split()
    if not words:
        return False
    base = words[0].rsplit("/", 1)[-1]
    if not base:
        return False
    return not shell_owns_the_pane(cmd)


def choose_done_oracle(state: dict | None) -> str:
    """Which oracle `wait --done` should use for this target.

    An oracle is named only when its evidence actually exists — the whole
    point of --done is that the agent does not have to know what the target
    supports, and it must never be told "fact" by a lucky screen scrape.
    """
    if state is None:
        return "quiet-unmanaged"          # no records: screen stability only
    if state.get("shell_integration"):
        return "probe" if not state.get("last_send_offset") else "osc133"
    if program_is_pane(state.get("cmd")):
        return "pane-exit"
    return "quiet"


DONE_VIA = {
    "osc133": "osc133",
    "probe": "shell-ready-probe",
    "pane-exit": "pane-exit",
    "quiet": "quiet-sampling",
    "quiet-unmanaged": "quiet-sampling-unmanaged",
}


def oracle_advice(state: dict | None, chosen: str | None) -> str | None:
    """How to get stronger evidence than the channel just used, if possible.

    Agents pick `--until` because it is the only flag they remember; the
    verdict is the moment to tell them what this target could prove instead.
    """
    if state is None:
        return ("unmanaged pane (term-use did not start it): shell-protocol "
                "facts are unavailable here, so --until/--quiet-ms run "
                "screen-only at heuristic confidence. For fact-level waits, "
                "launch the target with: term-use start -n NAME --cmd bash")
    if chosen == "until":
        if state.get("shell_integration"):
            return ("this session has OSC 133 shell integration: --done "
                    "(or --cmd-done) proves the command finished at fact "
                    "level and carries its exit code, while --until only "
                    "proves some text is on the screen. Add --since-send to "
                    "make --until match only output produced after the last "
                    "send (kills stale anchors).")
        return ("no shell integration in this session: anchor --until on the "
                "program's own output, or start with --cmd bash/--cmd zsh to "
                "get fact-level --done")
    if chosen in ("quiet", "quiet-unmanaged"):
        return ("quiet sampling is heuristic — it proves the pane stopped "
                "writing, not that the command exited. --cmd bash or --cmd "
                "zsh gets a fact-level oracle; for a program that IS the "
                "pane, wait --exit is fact.")
    return None


class ExitCondition(Condition):
    """Pane death via remain-on-exit: pane_dead / status / signal. fact."""

    label = "exit"
    confidence = "fact"

    def __init__(self, expect_code: int | None = None, managed: bool = True):
        self.expect_code = expect_code
        self.managed = managed
        self.exit_code: int | None = None

    def _meet_collapsed(self) -> bool:
        # Unmanaged pane whose session collapsed with it (no remain-on-exit):
        # the pane is gone, which is itself a tmux protocol fact. cmd_wait
        # probed the pane before constructing this condition, so a vanished
        # pane mid-wait means it died — meet with fact confidence, status
        # unreadable.
        if self.expect_code is not None:
            raise TDError(
                "expect-code-mismatch",
                f"pane vanished without remain-on-exit; exit code "
                f"unavailable (expected {self.expect_code})",
                hint="drop --expect-code for unmanaged panes, or keep the "
                     "program in a managed session (term-use start)")
        self.exit_code = None
        self.degraded = "status-unavailable"
        return True

    def evaluate(self, ctx) -> bool:
        try:
            m = ctx.meta()
        except TDError as e:
            if self.managed or e.code != "session-missing":
                raise
            return self._meet_collapsed()  # server gone: pane certainly gone
        if m.get("pane_dead"):
            signal = m.get("pane_dead_signal_name") or m.get("pane_dead_signal")
            if signal:
                raise TDError(
                    "pane-dead-by-signal",
                    f"pane killed by signal {signal}",
                    hint="respawn with: term-use start -n <name> --cmd "
                         "<command> (then re-run your scenario)",
                    evidence={"signal": signal, "screen": ctx.capture(0)},
                )
            self.exit_code = m.get("pane_dead_status")
            if self.expect_code is not None \
                    and self.exit_code != self.expect_code:
                raise osc133.mismatch_error(
                    self.expect_code, self.exit_code,
                    {"exit_code": self.exit_code, "expected": self.expect_code,
                     "screen": ctx.capture(0)})
            return True
        if not self.managed and m.get("pane_width") is None \
                and m.get("pane_dead") is None:
            # tmux answers a vanished pane target with rc=0 and EMPTY format
            # fields (no error) — that is the session collapsing with the pane
            return self._meet_collapsed()
        return False

    def to_json(self, met: bool) -> dict:
        return {**super().to_json(met), "expect_code": self.expect_code,
                "exit_code": self.exit_code if met else None}


def pane_dead_error(ctx) -> TDError:
    """Unexpected pane death during a wait that wasn't --exit (EOF early-termination)."""
    m = ctx.meta()
    signal = m.get("pane_dead_signal_name") or m.get("pane_dead_signal")
    if signal:
        return TDError(
            "pane-dead-by-signal",
            f"pane killed by signal {signal} while waiting",
            hint="respawn with: term-use start -n <name> --cmd <command>",
            evidence={"signal": signal, "screen": ctx.capture(0)},
        )
    return TDError(
        "pane-dead",
        f"pane exited (status {m.get('pane_dead_status')}) while waiting",
        hint="use --exit --expect-code N to wait for pane death on purpose",
        evidence={"exit_code": m.get("pane_dead_status"), "screen": ctx.capture(0)},
    )


class QuietCondition(Condition):
    """Animation-immune stability (heuristic).

    Two independent gates:
    1. Output activity: the raw o-stream's last event must be older than
       the quiet interval (immune to screen-content resonance, where a
       cyclic animation happens to repaint identical frames at sampling
       time).
    2. Screen stability: (screen, cursor_x/y, alternate_on, history_size)
       must be identical across two samples spaced >= interval apart.

    An unclosed CSI 2026 (begin synchronized update) forces instability.
    """
    label = "quiet"
    confidence = "heuristic"

    def __init__(self, session: str, ms: int, managed: bool = True):
        self.session = session
        self.managed = managed
        self.ms = ms
        self.interval = max(ms / 1000, 0.25)  # sampling floor: 250ms
        self._last = None
        self._open_2026 = 0
        self._last_o_ts = None  # elapsed of the newest o-event
        self._scan_offset = 0
        self._tail = ""
        if not managed:
            # Unmanaged pane: no raw.log, so gate 1 (stream last-write) cannot
            # run. Degrade to screen-stability sampling only — never touch
            # records (V2Writer would create a bogus state dir as a side
            # effect). Confidence stays heuristic either way.
            self.t0 = None
            self.degraded = "unmanaged"
            return
        w = records.V2Writer(session)
        self.t0 = w.t0
        self._consume_stream()

    def _consume_stream(self) -> None:
        path = records.session_dir(self.session) / "raw.log"
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        if size <= self._scan_offset:
            return
        text = ""
        for ts, code, data, _pos in records.events_since(self.session, self._scan_offset):
            if code == "o" and isinstance(data, str):
                self._last_o_ts = ts
                text += data
        self._scan_offset = size
        buf = self._tail + text
        for i in range(len(buf) - 7):
            if buf.startswith("\x1b[?2026h", i):
                self._open_2026 += 1
            elif buf.startswith("\x1b[?2026l", i):
                self._open_2026 = max(0, self._open_2026 - 1)
        self._tail = buf[-16:]

    def evaluate(self, ctx) -> bool:
        if self.managed:
            self._consume_stream()
        m = ctx.meta()
        sample = (ctx.capture(0), m.get("cursor_x"), m.get("cursor_y"),
                  m.get("alternate_on"), m.get("history_size"))
        if self._open_2026 > 0:
            self._last = sample  # never settle mid-synchronized-update
            return False
        # Gate 1 (managed only): real output activity — how long since the
        # pane emitted? 25% margin: a spinner whose frame period exactly
        # equals the quiet interval would otherwise settle at the instant
        # just before the next frame lands.
        if self.managed:
            if self._last_o_ts is None:
                return False
            idle = (time.time() - self.t0) - self._last_o_ts
            if idle < self.interval * 1.25:
                self._last = sample
                return False
        # Gate 2: screen snapshot stability across spaced samples.
        if self._last == sample:
            return True
        self._last = sample
        return False

    def to_json(self, met: bool) -> dict:
        return {**super().to_json(met), "ms": self.ms}


# Programs that host *another* terminal. Their shell's markers arrive as inert
# bytes for us, so the fact channel is structurally dead even while the local
# shell is perfectly healthy — the case that must never be reported as
# "inside an interactive program, send its quit key".
TERMINAL_PASSTHROUGH = ("ssh", "slogin", "mosh", "telnet", "tmux", "screen",
                        "dtach", "abduco")


def _managed_shell(state: dict | None) -> str:
    words = str((state or {}).get("cmd") or "").split()
    return words[0].rsplit("/", 1)[-1] if words else ""


def protocol_diagnosis(session: str, state: dict | None, meta: dict | None,
                       since_offset: int, ctx=None) -> TDError | None:
    """Name the reason a shell-protocol wait can never be satisfied, if there is one.

    Three states look alike from the outside and need different answers: the
    command is still running (wait), the marker channel is dead (change channel),
    the session is wedged (reconnect). Without this, the second one is reported
    as the first and the hint pushes agents into the third.
    """
    fg = str((meta or {}).get("pane_current_command") or "")

    def ev(extra: dict | None = None) -> dict:
        out = {"pane_current_command": fg or None}
        if ctx is not None:
            try:
                out["screen"] = ctx.capture(0)
            except (TDError, OSError):
                out["screen"] = None
        if extra:
            out.update(extra)
        return out

    if fg in TERMINAL_PASSTHROUGH:
        return TDError(
            "shell-protocol-unavailable",
            f"the pane's foreground program is {fg!r}: OSC 133 answers come from "
            f"the local shell, which is blocked inside it",
            hint="markers from a remote/nested shell arrive as ordinary bytes, so "
                 "--cmd-done/--shell-ready can never be satisfied here. Wait on "
                 "program output instead: --until 'regex' --since-send (only new "
                 "output), read it with `out`, or print your own sentinel "
                 "(printf 'DONE%s\\n' '$?'). Do NOT send a quit key: the shell "
                 "behind ssh may be idle and healthy.",
            evidence=ev())
    if state is not None and state.get("shell_integration"):
        if not records.markers_seen(session, 0, kinds=("C", "D")):
            return TDError(
                "shell-protocol-unavailable",
                f"session {session!r} was given shell integration but has emitted "
                f"no command markers at all",
                hint="the shell's own startup files likely replaced the injected "
                     "hooks (starship/powerline rewrite PROMPT_COMMAND every "
                     "prompt). Use --until 'regex' --since-send or print your own "
                     "sentinel; `trace -n " + session + " --format json` shows "
                     "what actually came through.",
                evidence=ev())
        if (fg in ("", _managed_shell(state))
                and not records.markers_seen(session, since_offset)
                and (records.output_age_s(session) or 0) > 1.0):
            return TDError(
                "shell-protocol-unavailable",
                f"the marker channel went silent: this session emitted OSC 133 "
                f"before, nothing since the last send",
                hint="a prompt framework likely took PROMPT_COMMAND back after our "
                     "injection. The pane has also produced no output for over a "
                     "second, so 'still running' is the less likely reading: "
                     "switch to --until 'regex' --since-send, or read `out`. "
                     "Do not send a quit key on the strength of this alone.",
                evidence=ev({"last_output_age_s": records.output_age_s(session)}))
    return None


def shell_ready_unmet(ctx, cond: "AllOf") -> bool:
    """Timeout diagnosis for --shell-ready: is the probe itself unanswered?

    AllOf's short-circuit `all()` can leave the ShellReady member
    unevaluated (its CmdDone records exit_code only on success), so
    re-evaluate it once here — the replay is idempotent. False means the
    shell DID answer: the timeout came from another condition (--until
    etc.) and must surface as plain timeout, not shell-not-ready (which
    would push agents to send a quit key to an idle shell).
    """
    sr = next(c for c in cond.conditions if isinstance(c, ShellReady))
    return not sr.evaluate(ctx)


def timeout_evidence(ctx, condition: Condition) -> dict:
    """Evidence snapshot for an unmet wait: screen, cursor, raw tail, state."""
    try:
        screen = ctx.capture(0)
    except (TDError, OSError):
        screen = None  # pane/session vanished before the snapshot
    try:
        m = ctx.meta()
        cursor = {"x": m.get("cursor_x"), "y": m.get("cursor_y")}
    except Exception:
        cursor = None
    raw_tail = ""
    if condition.__class__.__name__ == "AllOf":
        session = next((getattr(c, "session", None) for c in condition.conditions
                        if getattr(c, "session", None)), None)
        if session:
            try:
                raw_tail = records.stream_since(session, 0)[-300:]
            except (TDError, OSError):
                pass
    return {"screen": screen, "cursor": cursor, "raw_tail": raw_tail,
            "conditions": [condition.to_json(False)]}


def timeout_hint(condition: Condition) -> str:
    """Actionable next step for an unmet wait."""
    if condition.__class__.__name__ == "AllOf":
        quiet = next((c for c in condition.conditions if c.label == "quiet"),
                     None)
    else:
        quiet = condition if condition.label == "quiet" else None
    if quiet is not None:
        if getattr(quiet, "degraded", None) == "unmanaged":
            return ("quiet ran on an unmanaged pane (no raw.log) with "
                    "screen-stability sampling only — an animation or "
                    "clock repaint never settles; use --until on a program "
                    "marker instead")
        return ("quiet never settled — check raw.log for periodic writes "
                "(keepalive bytes reset the silence timer); use --until on a "
                "program marker instead, or raise --timeout")
    return "inspect evidence.screen; try a looser --until pattern or a longer --timeout"
