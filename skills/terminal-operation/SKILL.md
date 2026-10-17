---
name: terminal-operation
description: 终端操作 — drive, observe and verify real terminal programs. Use for any hands-on terminal work: running or reproducing commands, debugging interactive programs (tmux panes, full-screen TUIs like vim/htop/less, REPLs, ink/bubbletea apps, nested tmux), confirming a command finished and getting its exit code, selecting a menu item, reading what a screen shows, screenshotting a layout, and waits that must not be guessed
---

# Terminal operation (终端操作)

`term-use` drives a real program in a real tmux pane. Every `wait` returns a verdict with
a confidence level, every failure returns single-line JSON with the screen attached, every
observation says what it cost your context. CLI: `term-use`, or
`PYTHONPATH=<repo>/src python3 -m termuse`. Hard dependency: tmux. Pillow only for
`screenshot`.

## Five rules

1. **Wait, never sleep — default `wait --done`.** It proves the last thing you sent
   finished and carries its exit code. `--until` proves only that some text is on the
   screen and your own echo can supply it: use it for a marker you own, with
   `--since-send`. `--exit` when the program *is* the pane, `--shell-ready` to ask "is the
   shell back", `--quiet-ms` for animation with nothing to anchor on.
2. **Know what your evidence is worth.** `fact` > `inference` > `heuristic`; a composite
   verdict takes the weakest member. `--done` picks the strongest oracle the target
   supports and names it in `via`/`channel`; read `advice` before trusting a weak one.
3. **`--done`/`--cmd-done` are scoped to your last send** — the byte offset `send` returns.
   A marker from an earlier command cannot satisfy them, so they need no `--since-send`.
   `--until` does: it matches the current screen, stale text included.
4. **Cheapest channel that answers, by default the text ones.** `screen --grep/--find/
   --element-at` and `out` are exact and cheap. `screenshot` is for the one question text
   cannot answer — layout — once per layout question, never as a series, never to decide
   what to type.
5. **On a failure, read `hint`, then `evidence`.** The hint is the tool's diagnosis and
   usually holds the next command. Never send a quit key without looking at
   `evidence.screen` first.

## The loop

```bash
TD=term-use
$TD start -n demo --cmd bash --socket "$SOCK" --width 100 --height 30
$TD send  -n demo --socket "$SOCK" --type "make build" --key Enter
$TD wait  -n demo --socket "$SOCK" --done --expect-code 0 --timeout 120
$TD out   -n demo --socket "$SOCK"          # what it printed, inside a byte budget
```

`--socket NAME` belongs on **every** subcommand, not only on `sessions`; a private socket
with no server yet answers `[]` (with a note), not an error. `wait` defaults to
`--timeout 10 --interval 0.1` — 10s is a shell, not a build, so name a real number — and
`--done` is the recommended condition, not the CLI's default one; say it. Stop what you
start: `stop` (killing the last session takes the tmux server, and any nested sessions,
with it).

Two facts that save a call each: **a met `wait` verdict already carries the screen** in
`evidence.screen` (re-reading after a met wait is usually redundant), and **`trace`'s
header prints `started=`** — the recording's wall-clock origin — so `run` wrote that file
versus `check` did is answerable by comparing an mtime to `started + elapsed`, without
opening `raw.log` yourself.

### When the target is a plain CLI, not a TUI

- `--done` is the right wait even for a 50ms command; `--exit` is wrong (the pane is the
  shell and the shell does not die).
- **No output + `exit_code: 0` is a finding.** Report it. Do not re-send hoping for print.
- **Re-running is itself a write.** Before repeating a silent command, ask if it is
  idempotent — usually not; a second run corrupts state while both exits still read 0.
  Chain the proof onto the step instead of repeating it: `step && cat artifact`. One send
  then carries both the write and the read that shows it landed once — and a met `--done`
  on any compound line puts a `note` in the verdict saying which part its `exit_code`
  describes, because one number cannot cover the whole line.
- **Existence is not content.** `sqlite3.connect("x.db")` creates an empty file the moment
  a check opens it. Assert what only the program could have written (rows, a version
  pragma, a byte count), never a path. Reading program-written state is legitimate
  evidence; the red line is editing it.
- **A success string is not an oracle either.** A preflight (`check`, `--dry-run`, a status
  verb) that prints the line a real run prints makes the screen worthless as completion
  evidence — and `--until 'SYNC: COMPLETE' --since-send` can still be satisfied by it if
  the string is emitted after your send. Settle it by reproduction in a scratch copy: run
  the preflight there, then the real thing, and see which one creates the artifact. Two
  calls, and it ends the argument.
- `--quiet-ms` proves the *display* stopped changing, not that the program finished; while
  a command is in flight its verdict carries `program_running: true` and a `note`. When
  `--done` itself times out, read `evidence.in_flight`: true means it started and has not
  finished, so the next move is a longer `--timeout` or the input it is waiting for — never
  a looser `--until`.
- **Probe a stateful CLI without writing to it.** Ask for `--help`, or pass an unknown
  *flag* (`--nope`); do not probe with an unknown positional word — a program that takes
  one argument may accept `bogus` as legitimate input and record it. A refusal that prints
  `usage:` and exits 2 is the safe answer, and it names every subcommand at once.
- **When it is silent and persists, find the artifact by time, not by name.** `send`
  `touch .probe` before the run, then `find . -newer .probe` after it: the new file *is*
  the completion record. Open it read-only (`"file:x.db?mode=ro", uri=True`). Skip the
  probe when you already know what it writes — the point is the unknown case.

## Choosing a channel

| Question | Use |
|---|---|
| Did my command finish? what code? | `wait --done [--expect-code C]` |
| What did it print? | `out` (since your last send; `--all` for the session) — but after a chained `cmd && cat artifact` the met wait's own `evidence.screen` already holds the answer, so do not also call `out` |
| Which row is highlighted / what color / is X on screen / where do I click? | `screen --grep --attr reverse` / `--find TEXT` (1-based, click-ready) / `--element-at X Y` |
| Is anything on screen at all, or did my input never land? | `screen --meta` → `blank`, `unexecuted_input`, `last_output_age_s` |
| Is a command running right now? | `wait --done`'s failure says it (`evidence.in_flight`), a met `--quiet-ms` says it (`program_running`); `pane_current_command` is only a hint — measured on 3.7c it reported `bash` 1.2s into a `sleep 5` |
| How is this frame laid out? | `screenshot` — once |
| Something else must hold too | add it to the same `wait` (AND; weakest confidence wins) |

## Traps

- **`--enter` does not exist.** Text and keys are separate: `--type "cmd" --key Enter`.
- **"Never sleep" means never sleep *instead of waiting*.** `sleep 0.5-1` + `screen` is
  correct when nothing is waitable (timer-driven menus, unmanaged panes).
- **Echo-anchored waits lie.** Waiting on a substring of what you sent matches your echo —
  the verdict marks `echo_suspect` and drops to `heuristic`. Split the marker at runtime
  (`print('OUT_' + '42')`) or anchor on program output.
- **Your own shell eats `$?` / `$(…)` / backticks before tmux sees them.** `--type "echo
  exit=$?"` is quoted on *your* side, so it expands first and the pane faithfully runs a
  command you never wrote — the evidence then reads `exit=0` and is fabricated.
  Single-quote the whole value (`--type 'echo exit=$?'`), then read the echoed line back
  before believing the line under it.
- **Trailing spaces**: the terminal rstrips lines; `--until` self-heals, so do not write a
  regex that requires them.
- **Long typed text wraps** at the pane width, so a `--until` pattern spanning it must
  match the wrapped screen. `~/.../` on screen is the shell's PS1, not term-use — use
  `screen --meta`'s `pane_current_path` for the real cwd.
- **Confirmation keys**: wait for the prompt text itself before Enter — a key sent early
  lands in the edit buffer and corrupts state (measured on nano `C-o` + Enter).
- **Menu arrows debounce**: `send --key Up --key Up` in one call moves once. One key per
  call ~0.35s apart, or `--repeat 3 --delay 0.35`; verify the highlight landed by re-running
  the `--grep` before Enter. `less`'s search highlight is not `reverse` — pagers need a
  text anchor.
- **Spinners defeat quiet** (frame-cycle resonance): pick 2-3x the observed frame period;
  prefer a done-marker. Live monitors (top/htop) can be captured mid-repaint — read two
  frames ~2s apart and answer only if they agree, from the per-process rows.
- **REPLs**: any colon-opening statement enters the continuation prompt and needs one
  extra empty Enter. Prefer one-liners; anchor on the block's result, not the prompts.
- **A blank screen is a state**, not an error: `clear`, an exited full-screen program, or a
  quiet daemon. `screenshot` still writes a pane-shaped image and reports `blank: true`.
- **tmux can exit 0 and still have failed** (a default socket owned by someone else, an
  inherited `TMUX_TMPDIR` that does not exist here). term-use reports those as
  `socket-unreachable` with the fix; work on your own `--socket NAME`.
- `/tmp` may be unwritable in a sandbox: use `$TMPDIR` or the session's working directory.

## Failure codes

| Code | What it means | Do |
|---|---|---|
| `timeout` | the condition did not become true in time | read `evidence.screen`; re-anchor or raise `--timeout`; the target is still alive |
| `expect-code-mismatch` | it finished, with a different code | report the code — this is an answer, not a fault |
| `shell-not-ready` | something still holds the terminal (a REPL, a TUI) | read the screen, send that program's own quit key, re-probe |
| `shell-protocol-unavailable` | the fact channel **cannot** answer here (foreground is `ssh`/`mosh`/nested `tmux`, or the hooks were replaced) | change channel: `--until 'regex' --since-send`, or print your own sentinel. Never a quit key on this alone — check `evidence.screen`: an idle prompt means the hooks died, a program asking for input means send it |
| `unmanaged-pane` | term-use did not start that pane (no recording) | `--until`/`--quiet-ms`/`screen`/`send`/`clear` still work at degraded confidence; start your own session if you need fact-level waits |
| `no-shell-integration` | this shell emits no completion markers | `--cmd bash`/`--cmd zsh` for injected hooks, or wait on program output / `--exit` |
| `socket-unreachable` | tmux itself is unreachable — one fault reports one code, from `send`, `wait` and `stop` alike | honour the hint: `--socket NAME`, check `TMUX_TMPDIR`. If `sessions` answers `[]` while a server is alive (`pgrep -aft 'tmux.*NAME'`), the socket file was removed underneath it: `tmux -L NAME kill-server`, then start again |
| `pane-dead` / `pane-dead-by-signal` | the target died mid-wait | read the signal name from the evidence; respawn if needed |
| `pillow-missing` / `font-missing` | image channel unavailable | use the text channels (a font ships as a last resort, so this is rare) |

## Load a reference when you need it

Do not read these up front — each is for a moment you will recognize:

- `references/channels.md` — every subcommand and flag in detail: `screen -N/-J/--grid/
  --runs/--grep` attribute names, `out`/`replay`/`trace` output shapes, `screenshot`
  options, `clear`, `mouse-detect`/`click`, `fix-tty`, `resize`, context-budget knobs.
  Read it when a channel is not giving you what you expect.
- `references/tui-driving.md` — classifying an unfamiliar program, deriving an anchor from
  what it actually paints, finding the quit path, driving a wait-loop, REPLs, pagers,
  alternate-screen handovers. Read it before driving anything you have not already run.
- `references/nested-tmux.md` — a tmux client inside a term-use pane, `$TMUX`, addressing
  inner panes by socket name, and what degrades on unmanaged panes.
- `references/tmux-versions.md` — what tmux 3.8+ answers natively (`pane_command_running`,
  `pane_command_status`), what 3.2a–3.7c cannot answer, and why an absent key is the
  version answer rather than an empty one. Read it only if you are on 3.8 or the tool's
  fact waits are behaving oddly.

## Red lines

- Never bypass the CLI by editing files the program owns, and never write expected output
  into place. Observing the program is the product.
- Never claim a run happened without evidence: the `out`/`trace` text, and for layout claims
  the `screenshot` path.
- Timeouts are data: report the `error` JSON, do not paper over it.
