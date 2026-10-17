---
name: terminal-operation
description: 终端操作 — drive, observe and verify real terminal programs. Use for any hands-on terminal work: running or reproducing commands, debugging interactive programs (tmux panes, full-screen TUIs like vim/htop/less, REPLs, ink/bubbletea apps, nested tmux), confirming a command finished and getting its exit code, selecting a menu item, reading what a screen shows, screenshotting a layout, and waits that must not be guessed
---

# Terminal operation (终端操作)

## Overview

Interactive programs own a screen and react to keystrokes; stdout capture and blind `sleep`
both fail. `term-use` drives them through tmux and replaces guessing with **evidence**: every
wait returns a verdict with a confidence level, every failure returns structured JSON with a
screen snapshot attached, and every observation states what it costs your context.

This skill teaches a **method for unfamiliar programs** — observe, derive anchors from what the
program actually paints, confirm with probes — not a table of pre-known screens.

**Four rules, with the defaults filled in** — so nobody has to re-litigate this per session.

1. **Wait for state, then act. Default: `wait --done` — not `--until`.** Never sleep
   instead of waiting. `--done` proves the command finished and carries its exit code;
   `--until` proves only that some text is on screen, which your own echo can supply. Use
   `--until` for a marker you own and then with `--since-send`; use `--exit` when the
   program *is* the pane, `--shell-ready` to ask "is the shell back", `--quiet-ms` for an
   animated TUI with nothing to anchor on.
2. **Know what your evidence is worth.** `fact` (terminal protocol / pane death) beats
   `inference` (screen regex) beats `heuristic` (sampling). `--done` picks the strongest
   oracle the target supports and names the one it used in `via`; read `advice` before
   trusting a weak confidence.
3. **Take the cheapest observation that settles the question. Default: the text channels —
   not screenshots.** `screen --grep/--find/--element-at` and `out` are exact and cheap.
   `screenshot` answers the one question text cannot — layout — once per layout question,
   never as a series and never to decide what to type.
4. **On failure, read `hint` then `evidence`.** The hint is the tool's diagnosis and
   usually contains the exact next command.

CLI: `term-use` (installed) or `PYTHONPATH=<repo>/src python3 -m termuse` (only external
dependency: tmux; Pillow is needed by `screenshot` alone).

## Choosing what to look at

| Question | Use | Why |
|---|---|---|
| Did my command finish? exit code? | `wait --done` | fact level where the shell can prove it |
| Which item is highlighted? what color? is string X on screen? where do I click? | `screen --grep` / `--find` / `--element-at` | exact attributes, cheap |
| What did that command print? | `out` | scoped to your last send, inside a byte budget |
| What did the screen look like a moment ago? | `replay --at` | rebuilt through tmux itself from the recording |
| How is this frame laid out — where is the dialog, is the box aligned? | `screenshot` (once) | the one question pixels answer better |
| Is anything on screen at all? | `screen --meta` → `blank`, `unexecuted_input`, `last_output_age_s` | separates "empty" from "my input never landed" |
| Did a command start, and has it finished? | `screen --meta` → `pane_command_running` / `pane_command_status` (tmux 3.8+) | the start half is fact even through ssh; "running" also sticks at 1 when a shell merely waits for input |

Reported by the OSWorld benchmark — **their measurement, not ours**: agents given
**text/a11y observations scored 12.24% against 5.26% for screenshots**, and stacking
image history in the context is what failed. That is why rule 3 reads the way it does.
Nothing here says the numbers transfer to your model or your tasks: the claim this tool
makes for itself is narrower — a screenshot costs more context than the text channels
and answers fewer questions about a terminal.

## Quick reference

**Four commands carry almost every session: `start`, `send`, `wait --done`, `out`** —
plus `screen` when the question is about the screen and `screenshot` when it is about
layout. The rest of this table exists for the situations those six cannot answer; do not
learn it up front, look at it when something fails.

| Command | Purpose |
|---------|---------|
| `start -n N --cmd bash --width W --height H` | create a session (+ recording; bash/zsh get OSC 133 injected, fish ≥4 emits it itself) |
| `send -n N --type "text" --key Enter [--repeat K --delay S]` | inject input; text and key names are always separate tmux calls (mixing them in one call silently drops keys); `--repeat/--delay` for debounced menus and double-key rhythms |
| `wait -n N --done [--expect-code C]` | **the last thing you sent has finished** — strongest oracle available, verdict carries `via` + `exit_code` |
| `wait -n N --cmd-done / --shell-ready / --exit / --until 're' / --quiet-ms 800` | the individual oracles: OSC 133 done (fact), `true` probe (fact), pane death (fact), screen regex (inference), stability sampling (heuristic) |
| `wait … --until 're' --since-send` | match only output produced after the last send — no stale anchor can satisfy it |
| `screen -n N [--meta] [-N] [-J] [--grid] [--runs] [--grep --attr A] [--find TEXT] [--element-at X Y]` | text channel; `--find` → 1-based click-ready coords (screen columns, wide glyphs counted correctly). `-J` joins the lines tmux wrapped at the right edge back into one logical line (read a long command or a wrapped table); `-N` keeps the trailing cells tmux would strip, which is what preserves a painted background and column alignment |
| `out -n N [--all] [--max-bytes K] [--json]` | the pane's own byte stream since your last send, head+tail with the elision marked. It is the *pane*, so it contains your echoed input line and the prompt that follows — a trailing prompt with nothing after it means "the program said nothing", which is a result, not a failure to read |
| `replay -n N --at 3.5\|offset:N\|last-send [--format text\|json\|png]` | the screen at an earlier moment, rebuilt by feeding raw.log back through tmux |
| `screenshot -n N [-o shot.png] [--font-size 16] [--no-cursor]` | image channel, PNG only (`-o` must end in .png); `-o` optional — a numbered file beside the session's evidence that never overwrites an earlier shot; reports `glyphs.missing` honestly |
| `clear -n N [--history-only]` | reclaim the screen and scrollback before the next capture costs you again |
| `mouse-detect -n N` / `click -n N X Y` | mouse mode (SGR) / click at 1-based coords |
| `fix-tty -n N` | restore sane terminal (stty sane) after ECHO damage |
| `trace -n N [--format json]` | project raw.log. `text` (default) = a header line (`# v2 <WxH> cmd=…`) then `[ elapsed ] code NB 'preview'` per event, where `o` is pane output, `i` your injections, `m` sync points (`start`, `wait-met`); `json` = one asciicast-v2 array per line, `[elapsed, code, data]`, for parsing rather than reading. Usually you want `out` instead — trace is for *when* something happened, not what it said |
| `sessions [--socket NAME]` / `stop -n N` | list sessions / kill session |

Targets are `(socket, session, window, pane)`: `-n "sess"`, `"sess:win"`,
`"sock:sess:win.pane"`. A session living on a named socket must be spelled that way or
given `--socket`; `sessions --socket NAME` lists what exists. `wait` defaults:
`--timeout 10 --interval 0.1`. `--until` is Python `re` with DOTALL|MULTILINE.
`--grep` attr names: bold, dim, italic, underline, blink, reverse, strikethrough; fg/bg take
color names, 256-palette indexes or `#rrggbb`. Single-letter program keys are text
(`send --type q`); `--key` takes tmux key names only and errors with a table if you try one.

## The standard loop

```bash
TD=term-use   # or: PYTHONPATH=$REPO/src python3 -m termuse
$TD start -n demo --cmd bash --width 100 --height 30
$TD send  -n demo --type "make build" --key Enter
$TD wait  -n demo --done --expect-code 0 --timeout 120   # fact, or the best the target allows
$TD out   -n demo                       # what it printed, budgeted
```

`--done` picks: OSC 133 done-marker (fact, with exit code) → the `true` probe when nothing has
been sent yet → pane death when the program *is* the pane → quiet sampling (heuristic, and it
will say so). The verdict's `via` names the channel and `advice` points at a stronger one when
the target could support it — read `advice` before you trust a weak confidence.

Two servers can answer the done-marker oracle: term-use's own scan of the recording
(`via: osc133`) and, on **tmux 3.8+**, the command status tmux keeps for the pane
(`via: tmux-command-status`; `screen --meta` then carries `pane_command_running`,
`pane_command_status`, `pane_command_duration`). The native one answers where the
recording cannot: a pipe-pane that stopped, a prompt framework that took
`PROMPT_COMMAND` back, or markers forwarded through ssh. On 3.2a–3.7c those keys are
*absent*, not empty — absence is the version answer, and `tmux -V` states it directly
when you need to say it out loud. If your target is on an older server, ignore the
`pane_command_*` rows below rather than hunting for them: nothing you can do here
turns them on.

When the fact channel is **structurally** unable to answer you get
`shell-protocol-unavailable` instead of a timeout: the pane's foreground is a terminal
passthrough (`ssh`, `mosh`, nested `tmux`) whose remote markers reach our recording as
ordinary bytes, or the injected hooks were replaced at startup. It means *change channel*
(`--until 'regex' --since-send`, or print your own sentinel) and explicitly **not**
"send the program's quit key" — the shell behind ssh may be idle and healthy.

Read `evidence.native` with it, because that error covers two different situations and
only you can tell them apart from the screen: a shell **waiting for input** (`read`, a
password prompt, a menu — send the input it wants) and a completion hook that **died**
(a prompt framework rewrites `PROMPT_COMMAND` every prompt while `PS0` keeps emitting
`C`; switch channel). Both leave `pane_command_running: true` and a silent pane, so
neither the marker stream nor tmux can separate them — do not send a quit key without
looking at `evidence.screen` first.

A bare `--cmd-done` right after `start` always times out: bash's startup emits a `D` with no
preceding `C`, which reads as aborted. `--done` handles that case by probing; if you use
`--cmd-done` directly, probe with `--shell-ready` first. The probe doubles as "is the shell
back?" after leaving a nested program — a live REPL swallows it, so it fails with
`shell-not-ready` and the evidence shows the probe sitting on that program's prompt.

Wrong `--expect-code` fails immediately (`expect-code-mismatch`), never after burning the
timeout; pane death mid-wait fails immediately with `pane-dead` / `pane-dead-by-signal`
(signal names are normalized — tmux answers "9" on some builds, "KILL" on others).

## When the target is a plain CLI, not a TUI

Everything further down is about screens that move. A `bun todo.js done 2` run inside a
managed bash pane has different rules, and getting them wrong costs calls:

- **Wait with `--done`.** The command finishes in milliseconds and `--done` still returns
  `fact` with the real exit code — no oracle is stronger for this shape, and you do not
  have to choose. `--exit` is the wrong one here: the pane *is* the shell and the shell
  is not going to die.
- **"It printed nothing and exited 0" is a finding, not a failed read.** That is exactly
  what a silent no-op looks like: `out` shows your echoed command and the next prompt
  with nothing between them. Report that as behavior. Do not re-send hoping it prints
  something next time, and do not conclude your input was swallowed — `exit_code: 0`
  says the shell ran it.
- **Anchor on the program's own bytes, never on the prompt.** The prompt repaints after
  every command, so `--until '$ '` is already true before you send anything.
- **When the program writes an artifact, read the artifact.** A CLI that persists
  `todos.json`/a db/ a file has the truth there; `send --type 'cat todos.json'` in the
  same pane puts it in the recording, which is stronger evidence than the screen text.
- **`clear -n N` between repro and verification** keeps the verification reads from
  carrying the whole earlier session with them.

## Keeping the context bill down

Every observation returns a `context` block: `bytes` (exact), `est_tokens` + `method`
(estimate — `bytes/4` for text, pixels/750 for an image), `budget`, `over_budget`. When a
capture is over budget the tool says so on stderr and names the fix.

- A fat pane is not free: `capture-pane` shows what is on screen *plus* what scrolled, so every
  later read re-carries the same bytes. When warned, run `clear -n N` (or start a fresh session)
  before the next capture.
- Prefer the narrowest channel that answers: `--grep`/`--find` over a full `--grid`; `out` over
  `screen --scrollback 500`; `--json`/`--text` output over piping a whole screen into your eye.
- One screenshot per layout question. Images are the most expensive thing you can put in
  context and the least useful to accumulate.
- `TERM_USE_CONTEXT_BUDGET` retunes the threshold to your host.

## Common Mistakes

- **`--type "cmd" --enter`**: there is no `--enter`. Text and keys are separate calls and Enter
  is a key: `--type "cmd" --key Enter`.
- **"Never sleep" means never sleep *instead of waiting*.** Sleep is legitimate as the pace of a
  polling loop when nothing is waitable (timer-driven screens, menus, unmanaged panes):
  `sleep 0.5-1` + `screen`. Pick the shortest pause that outlasts one observed repaint period.
- **Echo-anchored waits are untrustworthy.** You see your own typed text on screen, so
  `wait --until` on a substring of what you sent is your echo, and the verdict says so
  (`echo_suspect: true`, confidence downgraded to `heuristic`). Never put the marker you wait for
  inside your sent text — split it at runtime (`print('OUT_' + '42')`) or anchor on program
  output / `--done`. When a word is unavoidably both input and output (pager searches, commands
  echoing filenames), don't wait on it: act, then verify by reading the screen.
- **Stale anchors.** `--until` matches the *current* screen, so an anchor left by an earlier
  command satisfies a new wait instantly. Use `--since-send` to match only new output, or
  `--done`.
- **Your own shell eats the text before tmux sees it.** `--type "echo exit=$?"` is quoted at
  *your* side, so `$?` (or `$(…)`, or backticks) is expanded by the shell running term-use and
  the pane faithfully runs the mangled command. The damage is that the evidence then looks
  valid: the output really did say `exit=0`, it just never came from the program you meant to
  test. Single-quote the whole value — `--type 'echo exit=$?'` — and when a result depends on
  shell syntax, read back the echoed command line in `out`/`evidence.screen` before believing
  the line under it.
- **Trailing spaces.** The terminal rstrips lines, so `'your name: '` won't match raw text.
  `--until` self-heals (matches rstripped text plus a sentinel space) — trust it instead of
  writing a regex that requires trailing spaces to survive.
- **Confirm keys on a fixed sleep.** Before EVERY TUI confirmation key (Enter on a save prompt or
  menu choice), wait for the prompt text itself — a key sent before the prompt appears lands in
  the edit buffer and corrupts state (measured: nano `C-o` then immediate Enter cost an extra
  round-trip).
- **Spinners defeat quiet.** An animated line changes every ~150ms but a slow device can sample
  identical frames at exactly that period (frame-cycle resonance). `--quiet-ms` also gates on the
  raw stream's last-write time (25% margin), so choose 2-3x the observed frame period, anchor on
  a "done" marker where one exists, and don't trust a large quiet value across a phase where the
  program may only *start* thinking after your input. Menu screens are quiet-hostile too (modal
  pickers repaint on a timer): `sleep 0.5-1` + `screen` inside menus.
- **Menu arrows debounce.** `send --key Up --key Up` in one call moves the selection **once** —
  one arrow per call, ~0.35s apart, or `send --key Down --repeat 3 --delay 0.35`.
- **Selecting item N in a menu** (recipe): read the highlight with `screen --grep --attr reverse`
  → send the arrows with `--repeat` → **re-run the grep to verify the highlight landed where you
  intended** before Enter. Attribute channels don't cover every program: less's search highlight
  does not present as `reverse` — for pager-style highlights verify by plain text match.
- **Live-monitor TUIs (top/htop-style) repaint on a timer.** One captured frame can be
  mid-repaint, and short-lived processes vanish between refreshes — read two frames ~2s apart and
  answer only if they agree. Answer from the program's own sorted rows, not aggregate header lines
  (measured: `800%cpu 800%idle` while per-process rows were still usable).
- **REPL block input.** ANY colon-opening statement enters the continuation prompt (`...`), and
  the block needs ONE extra empty Enter to close, otherwise your next line is swallowed. Prefer
  colon-free one-liners; for real blocks send each line separately and anchor on the block's
  *result*, not the prompts.
- **Stuck / echo-less screen.** A program may have mangled the tty (`stty -echo`); `send` warns
  `tty-echo-broken` when your text produces no echo bytes. Run `fix-tty`. When a session looks
  hung, check `screen --meta`: `unexecuted_input` non-empty with a growing `last_output_age_s`
  means your keystrokes are landing on a line nothing is reading — that is a different diagnosis
  from a program that is merely quiet.
- **Keys without names.** `C-\` has no tmux key name: send raw bytes `send --hex 1c`. Multi-byte
  sequences work (`--hex 1b5b31333b3275` = kitty Shift+Enter); the CLI splits one byte per `-H`
  because tmux 3.x drops multi-byte `-H`.
- **Mouse apps**: `mouse-detect` first; `click X Y` is 1-based while curses programs print 0-based
  coords.
- **Long typed text wraps.** A ~90-char `--type` wraps on a 100-col pane, so a `--until` pattern
  spanning it must match the *wrapped* screen — or keep commands short and let the program print
  the marker. `evidence.screen` shows the prompt verbatim: `~/.../` is the shell's PS1, not
  term-use; use `screen --meta`'s `pane_current_path` for the real cwd.
- **Blank screens are a state.** After `clear`, or when a full-screen program has exited, or when
  a daemon prints nothing, the pane legitimately shows nothing: `screenshot` still writes a
  pane-shaped image and reports `blank: true`, and `screen` says "no text" rather than failing.
- **Throwaway files.** On sandboxed platforms `/tmp` may be unwritable (measured on Android):
  use `$TMPDIR` or the session's working directory.
- **Leaking sessions.** Always `stop`. Stopping the last session kills the whole tmux server,
  taking nested sessions with it.
- **Inherited terminal environment.** tmux can print an error and still exit 0 (a default socket
  owned by another user, a `TMUX_TMPDIR` pointing somewhere that does not exist here). term-use
  reports those as `socket-unreachable` with the fix; on a shared machine, work on your own
  `--socket NAME`.

## Nested tmux

A tmux client running *inside* a term-use pane is directly drivable — with three measured
gotchas:

1. **`$TMUX` blocks the nested client** — tmux refuses to start ("sessions should be nested with
   care, unset $TMUX to force"). Run `unset TMUX; tmux new-session -s inner ...`.
2. **Address inner panes by socket name**: the default socket's name is literally `default` —
   `screen -n default:inner:0.0` works; a bare `-n inner` does NOT (it looks for a term-use-managed
   session and fails with `session-missing`).
3. **wait on unmanaged panes** (term-use didn't start them): `--until`/`--quiet-ms` work with
   degraded confidence — capped at `heuristic`, `"degraded": "unmanaged"` in the verdict, and raw
   echo detection off (anchor on output that isn't a substring of your input).
   `--cmd-done`/`--shell-ready`/`--since-send`/`out` refuse with `unmanaged-pane` (they need the
   recording). `--exit` works even when the pane's death takes its session with it: met/fact with
   `"degraded": "status-unavailable"` and `exit_code: null` — so don't pass `--expect-code` there.
   `send`, `screen`, `clear` work unchanged.

## Driving an unfamiliar interactive program

Work in this order — observe before you anchor, anchor before you drive:

**1. Classify the program** — it decides how completion and exit are confirmed:
   - *Program IS the pane* (`start --cmd <prog>`, including `bash script.sh` / `sh -c '…'`): its
     death is observable → `wait --exit` (fact; works for non-bash too).
   - *Program inside bash* (`send <prog> --key Enter` in a bash pane): the pane always belongs to
     bash → `--exit` never fires; "did it exit?" is answered by `wait --shell-ready` (it fails
     with `shell-not-ready` while the program still holds the terminal — send its quit key, then
     re-probe). **Do NOT probe with `pgrep`** — term-use's own recorder is a python3 process, so a
     process count is never 0 (measured: 4 in a bare session).
   - *REPL* (prompt + eval): the prompt is the anchor; results are verified by reading
     `out` / `screen -J | grep`, never by waiting on a substring of what you sent.
   - *Full-screen TUI* (alt-screen, repaints): prompt-less; anchors come from observation (step 2).
     After it exits, the alt-screen restores and stale frames can fake-settle short quiets and
     re-match old anchors — re-verify the screen after any program swap; anchor the new program's
     first paint.

**2. Observe, then derive the anchor.** Launch, then catch the first paint before you have any
   anchor: `wait --quiet-ms 700` (non-animated programs settle fast) or `sleep 1` + `screen` for
   timer-driven ones — read what the program ACTUALLY paints, then wait on it. Good anchors, in
   rough strength order: dialog text the program prints itself, status-line vocabulary shown only
   in one phase, prompt shapes (`^>>>`, `^>$`). Bad anchors: substrings of your own input, and
   text you know from a *different* program's manual — every anchor belongs to the program it was
   measured on (measured drift: nano 9.2's save prompt is `Write to File:`, older nano said
   `File Name to Write`). Derive yours from your program's screen; when an anchor times out, read
   `evidence.screen` and re-anchor. Set `--timeout` from a first measured run (×3-5 headroom;
   raise it for slow cold starts — measured: a Node CLI took ~20s to first paint on a phone).

**3. Find the exit path before you need it.** Check `--help`/man for the quit key; common
   conventions are `q` (pagers), `C-d` (REPLs), `C-c` (interrupt), `Escape` ×N (menus) — verify on
   screen rather than assuming. After the quit keystroke, confirm the shell took input back with
   `wait --shell-ready`. Double-tap rhythms are program-specific: `--repeat 2 --delay 0.3` sends
   the pair as two independent calls in one invocation; measure which rhythm works, and err short,
   since each CLI invocation's startup inflates the gap.

**4. Drive in a wait-loop.** One action → wait for its observed effect → next action. When nothing
   new is waitable (input produces no fresh output, answers finish before the streaming phase is
   catchable), stop burning waits and poll `screen` for evidence instead.

## Anti-cheating red lines

- **Never** bypass the CLI by editing files the program owns, or by `Write`-ing the expected
  output into place. The whole point is observing the program's real behavior.
- **Never** claim a run happened without evidence: attach the `out`/`trace` text and, for layout
  claims, the `screenshot` path.
- Timeouts are data: report the `error` JSON (screen snapshot inside), don't swallow it.

## Tester feedback protocol

If you were handed this skill as a blind tester: you have the right to complain — after any task,
append a line `FEEDBACK: <what confused you / what was missing / what you had to guess>` to your
output. Complaints feed directly into this skill's next revision.
