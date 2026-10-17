# Using term-use

Complete guide for driving and debugging interactive terminal programs with
term-use. Written for AI agents first — humans debugging a TUI will find it
works the same way.

## The three rules

1. **Wait for state, then act.** Never sleep, never guess a delay. After every
   action, `wait` for the condition you expect before the next one.
2. **Know what your evidence is worth.** `fact` (terminal protocol) beats
   `inference` (screen regex) beats `heuristic` (quiet sampling). Prefer fact;
   treat heuristic as "probably safe". `--done` makes that choice for you.
3. **Take the cheapest observation that settles the question.** Attribute and
   text questions ("which item is selected", "what color", "where do I click") →
   `screen`; "what did it print" → `out`; "what did the screen look like then" →
   `replay`. Spatial/layout questions → `screenshot`, once. Every result says
   what it cost your context.

## The basic workflow

```bash
TD="term-use"   # or: PYTHONPATH=/path/to/repo/src python3 -m termuse

$TD start -n demo --cmd bash --width 100 --height 30
$TD send  -n demo --type "make build" --key Enter
$TD wait  -n demo --done --expect-code 0 --timeout 120
# → {"verdict": "met", "confidence": "fact", "via": "osc133", "exit_code": 0, ...}
$TD out   -n demo                       # what it printed, inside a context budget
```

That is the whole loop: **start → send → wait for fact → read the output → act.**

- `--done` waits for the last thing you sent to finish, choosing the strongest
  oracle the target actually supports and reporting it in `via`: the shell's
  OSC 133 done-marker (fact, with exit code) → the `true` probe when nothing has
  been sent yet → pane death when the program *is* the pane → quiet sampling
  (heuristic, and it says so). `advice` names a stronger channel when the target
  could support one.
- The done-marker oracle has two servers. term-use scans its own recording
  (`via: osc133`); on **tmux 3.8+** the CLI asks tmux first, and when that is
  what answered you get `via: tmux-command-status` plus a `channel` key naming
  it. The native answer is the one that survives a stopped recorder, a prompt
  framework that rewrites `PROMPT_COMMAND`, and markers forwarded through ssh —
  which is why 3.8+ is the recommended tmux.
- When no fact channel can answer, that is said out loud: `--cmd-done` /
  `--shell-ready` / `--done` raise `shell-protocol-unavailable` (the pane's
  foreground is `ssh`/`mosh`/a nested `tmux`, or the injected hooks were
  replaced) instead of timing out and hinting at a quit key. `evidence.native`
  then carries tmux's own reading — but `running: true` does **not** mean "your
  command is still going": a shell blocked on `read` and a shell whose completion
  hook was taken over both look like that. `evidence.screen` is what separates
  them (a program asking for input vs an idle prompt).
- `--shell-ready` is that probe on its own. After leaving a nested program it
  confirms the shell took input back — a live interactive program swallows the
  probe, and the wait fails with `shell-not-ready` instead of pretending.
- The shell protocol behind `--cmd-done` is injected for bash and zsh; fish ≥ 4.0
  emits the markers itself and term-use notices rather than assuming. A shell
  that cannot emit them (plain `sh`/`dash` — no pre-exec hook) gets a structured
  `no-shell-integration`, so fall back to `--until` on the program's own output,
  or `--exit` on pane death.
- A wrong `--expect-code` fails **immediately** with `expect-code-mismatch` —
  no timeout burn.
- Pane death mid-wait fails immediately (`pane-dead` / `pane-dead-by-signal`
  with a respawn hint).

## Usage

### Choosing a wait condition

**Default: `--done`.** Every other condition is the answer to a *different* question, and
reaching for `--until` first is the habit this tool exists to break.

| Situation | Wait with |
|---|---|
| Did the thing I just sent finish? what code? | **`--done [--expect-code N]`** — picks the strongest oracle this target supports and reports `via` + `exit_code` |
| Is the shell ready / back in control? | `--shell-ready` — the `true` probe (fact); timeout → `shell-not-ready` |
| Did the shell command finish? what code? | `--cmd-done [--expect-code N]` — the done-marker channel `--done` uses (term-use's scan, or tmux's own status on 3.8+) |
| Did the program exit or crash? | `--exit [--expect-code N]` (signal name in evidence) |
| A known output marker appears | `--until 'regex'` (`--scrollback N` extends history) |
| …and it must be *new* output, not text left on screen | `--until 'regex' --since-send` |
| No marker exists (animated TUI) | `--quiet-ms 800`, at 2-3x the observed frame period |
| Several things must hold | pass multiple conditions in one `wait` (AND; verdict takes the weakest confidence) |

`--done` exists because `--until` is the flag people reach for and the weakest
of the ones that fit: a screen regex proves *some text is on screen*, which your
own echo can satisfy. Where the shell can prove completion, `--done` uses that;
where it cannot (a non-integrated shell, an unmanaged pane) it degrades to quiet
sampling and says so, with `advice` naming what would give you more.

```bash
$TD send -n demo --type "make build" --key Enter
$TD wait -n demo --done --expect-code 0 --timeout 120
# → {"verdict":"met","confidence":"fact","via":"osc133","exit_code":0, ...}
```

### Typing text, keys, raw bytes

```bash
$TD send -n demo --type "y" --key Enter          # text and keys are SEPARATE calls, always
$TD send -n demo --key C-o                        # control key (nano save)
$TD send -n demo --hex 1c                         # raw byte (C-\); no key name exists
$TD send -n demo --hex 1b5b31333b3275             # multi-byte: kitty Shift+Enter
```

Text and keys are issued as separate `send-keys` calls because tmux silently
drops keys when `-l` text and key names are mixed in one call — a documented
tmux failure mode this CLI refuses to let you trigger. `--hex` is split one
byte per `-H` (tmux 3.x drops multi-byte `-H`), so escape sequences arrive
contiguously within a single call.

TUI menus **debounce arrow keys**: `send --key Up --key Up` in one call moves
the selection **once**. Let the tool do the rhythm — each repetition is a full
independent send with a precise gap:

```bash
$TD send -n demo --key Up --repeat 3              # 3 arrows, default 0.35s apart
$TD send -n demo --key C-c --repeat 2 --delay 0.3 # double-key exit rhythm
```

The default `--delay 0.35` is the gap measured to clear real menu debouncing;
every repetition is a separate `send-keys` call (never merged — merging is what
menus eat).

If `send` warns `tty-echo-broken` (your text produced no echo bytes), the
program mangled the tty — run `fix-tty`.

### Mouse-driven TUIs

```bash
$TD mouse-detect -n demo          # report the pane's mouse tracking mode
$TD click -n demo 12 40           # SGR click, 1-based coords
```

`click` is 1-based while curses programs print 0-based coords — mind the
off-by-one.

### Observing: text, output, history, image

**Default: the text channels.** `screenshot` is for the one question text cannot answer —
layout — and it is the most expensive thing you can put in context. Not invented: the
OSWorld benchmark reports **12.24% for text/a11y observations against 5.26% for
screenshots**, and it was the accumulated image history that broke them (their
measurement, not ours — treat it as direction, not as a number for your model). So one
picture per layout question, never a series, and never to decide what to type.

```bash
$TD screen -n demo --meta                          # cursor, modes, history, blank, stall signals
#    on tmux 3.8+ also pane_command_running / pane_command_status / pane_command_duration:
#    "a command started and has not finished" is fact there, even through ssh — but
#    running:true also describes a shell merely waiting for input, so read
#    evidence.screen before acting on it. Absent (not empty) on 3.2a–3.7c.
$TD screen -n demo --runs                          # SGR attribute runs (token-cheap)
$TD screen -n demo --grep --fg red                 # attribute search (flag, not a text grep)
$TD screen -n demo --grep --attr reverse           # e.g. locate a menu's highlighted row
$TD screen -n demo --find "Cancel"                 # literal text → 1-based coords
$TD screen -n demo --element-at 40 12              # what is this cell?
$TD out -n demo                                    # what printed since my last send, budgeted
$TD out -n demo --json --max-bytes 4000            # structured, with the elision counted
$TD replay -n demo --at 3.5                        # the screen as it was at t=3.5s
$TD replay -n demo --at last-send --format png -o old.png
$TD screenshot -n demo                             # spatial layout (needs Pillow; -o optional)
$TD clear -n demo                                  # reclaim screen + scrollback before the next read
$TD trace -n demo                                  # project raw.log: input/output/sync events
```

`screen --find` returns every literal match with **1-based, click-ready**
coordinates — the locate-then-click flow is a single hop:

```json
[{"text": "Cancel", "x": 12, "y": 7}]
```

```bash
$TD click -n demo 12 7                             # feed the coords straight in
```

Columns are counted the way the pane counts them: a double-width glyph (CJK,
emoji) occupies two columns and tmux emits no padding cell for the second one, so
`--find`/`--element-at`/the image all agree with what you see rather than with the
character count. Cells carry `w`, and the reserved half appears as a `pad` stub.

**Which channel.** Text channels are exact and cheap; the image channel answers
the one question they cannot — *how is this frame laid out* — and costs more every
time you look at it. Against the intuition that a multimodal model should prefer
pictures, the OSWorld benchmark reports agents on text/a11y observations scoring
12.24% versus 5.26% on screenshots, with stacking screenshot history what degraded
there (their numbers, not ours). So: text by default, one screenshot per layout
question, never a running series.

**Every observation states its cost.** JSON results carry a `context` block —
`bytes` (exact), `est_tokens` + `method` (an estimate: `bytes/4` for text,
pixels/750 for an image), `budget`, `over_budget` — and an over-budget capture
adds a stderr note naming `clear` as the way out, because a fat pane is
re-carried by every later read. `TERM_USE_CONTEXT_BUDGET` retunes the threshold.

`screenshot` reports what it could not draw (`glyphs.missing`, with codepoints)
instead of quietly emitting tofu: blocks, box drawing, Powerline separators and
braille are drawn as geometry, other characters go through a font chain resolved
by each face's own cmap, and the cursor is outlined unless you pass `--no-cursor`.

`screen --meta`'s `pane_current_path` is the real cwd; the prompt on screen may
abbreviate (`~/.../`) — that is the shell's PS1, not a display bug. Its
`blank` / `unexecuted_input` / `last_output_age_s` answer the question a blank
screen otherwise leaves open: is the program quiet, or did my input never land?

### Nested tmux and unmanaged panes

Panes inside a nested tmux are addressable directly by their socket name —
`sock:sess:win.pane` — and `send` / `screen` / `click` work on them like on any
pane. A bare session name addresses the default tmux server exactly like plain
tmux does, so unmanaged sessions there resolve the same way (no `state.json`
needed). Unmanaged targets are, however, **not managed** by term-use (no
`state.json`, no `raw.log`), and waits degrade honestly instead of pretending:

| Wait | On an unmanaged pane |
|---|---|
| `--until` | works, screen polling only — echo-suspect detection is impossible, so confidence is capped at `heuristic` and the verdict condition carries `"degraded": "unmanaged"` |
| `--quiet-ms` | works, screen-stability sampling only (no raw-stream gate) — same `degraded` marker |
| `--exit` | works — read-only; if the pane's session collapses with it (no remain-on-exit), pane death is still a `fact`: verdict met with `"degraded": "status-unavailable"` (no exit code readable, so drop `--expect-code` there) |
| `--cmd-done` / `--shell-ready` | refuses with `unmanaged-pane` — there is no shell-protocol channel; start a managed bash session if you need facts |

term-use never creates record state for unmanaged panes.

### Reading failures — timeouts are data

Every failure prints **single-line JSON on stderr** with a screen snapshot
attached:

```json
{"error":{"code":"timeout","message":"...","evidence":{"screen":"...","cursor":{"x":1,"y":5},"raw_tail":"..."}}}
```

Never swallow it, never paraphrase it away. A timeout does **not** kill the
target — inspect the snapshot and retry with a better anchor.

Two codes are deliberate signals, not noise: `shell-not-ready` (the `true`
probe was swallowed — you are probably inside an interactive program; send its
quit key and re-probe) and `unmanaged-pane` (the target has no term-use
records — see the table above).

## How it works

```
agent → term-use CLI (stateless client) → tmux server → pane (target program)
                                               └→ pipe.py (recorder sink, one per pane)
```

The CLI is a short-lived, stateless client with zero resident memory; the tmux
server owns PTYs/sessions. All state lives on disk under
`$XDG_STATE_HOME/term-use/<session>/`:

- **`raw.log`** — asciicast v2 evidence store (`o` pane output, `i` injections,
  `r` resize, `m` sync points). The ground truth for protocol-level bytes.
- **`state.json`** — locator + recorder state, written atomically (tmp + rename).
  `last_send_offset` is a **byte offset** into `raw.log`, so `--cmd-done` only
  accepts markers emitted after your last send — a typed echo can never fake
  completion.

Every mechanism claim in this guide is exercised by the E2E suite
(`tests/e2e/*.sh`), which drives real tmux and real programs through the CLI
only — nothing is mocked.

## Common mistakes

- **`--type "cmd" --enter`** — v2 has no `--enter`. Always
  `--type "cmd" --key Enter`.
- **Trailing spaces** — the terminal rstrips lines; `--until` self-heals, but
  never write regexes that *require* trailing spaces to survive.
- **Echo-anchored waits are untrustworthy** — you see your own typed text on
  screen. If the matched text is a substring of what you last sent, the verdict
  is flagged `echo_suspect: true` and confidence drops to `heuristic`. Never
  put the marker you wait for inside your sent text; split it at runtime
  (`print('OUT_' + '42')`). Leftover echoes from earlier experiments pollute
  later waits — use a fresh session for sensitive experiments.
- **Spinners defeat quiet** — `--quiet-ms` also gates on the raw stream's
  last-write time, but pick 2-3x the frame period and anchor with `--until`
  when possible. A program that *starts* spinning after your input can
  false-settle a large quiet value — wait for the stream to start first.
  **Menus are quiet-hostile**: inside modal pickers use `sleep 0.5-1` +
  `screen`, not `--quiet-ms`. Menu arrow keys also debounce: `send --key Up
  --key Up` in one call moves the selection **once** — use
  `send --key Up --repeat N --delay S` (each repetition a separate send with a
  precise gap).
- **Long typed text wraps** — a ~90-char `--type` wraps on a 100-col pane; a
  `--until` pattern spanning that text must match the *wrapped* screen, or keep
  sent commands short and let the program print the marker.
- **Leaking sessions** — always `stop` when done (or `tmux kill-server` in test
  cleanup).

## Red lines (anti-cheating)

- **Never** bypass the CLI by editing files the program owns or by writing the
  expected output into place. The point is observing real behavior.
- **Never** claim a run happened without evidence: attach `trace` output or
  `raw.log` events, and for layout claims the `screenshot` path.
- Timeouts are data: report the `error` JSON, don't retry until it "passes".

## Worked example: nano edit + save

```bash
$TD start -n nano-demo --cmd bash --width 100 --height 30
$TD wait  -n nano-demo --shell-ready --timeout 10       # shell ready (fact)
$TD send  -n nano-demo --type "nano notes.txt" --key Enter
$TD wait  -n nano-demo --until 'GNU nano' --timeout 15  # TUI is up
$TD send  -n nano-demo --type "hello from an agent"
$TD send  -n nano-demo --key C-o                        # save
$TD wait  -n nano-demo --until 'File Name to Write' --timeout 10
$TD send  -n nano-demo --key Enter                       # confirm filename
$TD wait  -n nano-demo --cmd-done --expect-code 0 --timeout 15   # back at shell (fact)
$TD stop  -n nano-demo
```

## Getting help

- Issues: https://github.com/uobe1/term-use/issues
- Install problems: [installation.md](installation.md)
- Full scenario library: [SKILL.md](../../skills/terminal-operation/SKILL.md)
