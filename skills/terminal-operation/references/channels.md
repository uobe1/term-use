# Channel reference — every subcommand and flag

Loaded when a channel is not giving you what you expected. Nothing here needs to be known
up front; the loop in `SKILL.md` is enough for most sessions.

## Targets

`(socket, session, window, pane)`: `-n "sess"`, `"sess:win"`, `"sock:sess:win.pane"`.
A session on a named socket must be spelled that way or given `--socket`; `--socket` works
on every subcommand. Nested tmux is addressed by socket name — the default socket's name
is literally `default` (`-n default:inner:0.0`).

## start / stop / sessions / resize

| Command | Notes |
|---|---|
| `start -n N --cmd bash --width W --height H` | creates the session and its recording. bash/zsh get OSC 133 hooks injected; fish ≥4 emits them itself; plain `sh`/`dash` have no pre-exec hook but `send` can narrate them with its sentinel; an unrecognized command name gets neither. The target is gated until the recorder is attached, so a program's first bytes are never lost. The result carries `marker: shell|sentinel|unknown` — which channel it expects; `unknown` means the first send or `--shell-ready` will tell. |
| `stop -n N` | kills the session. Stopping the last one kills the tmux server, taking nested sessions with it. |
| `sessions [--socket NAME]` | `[{name, windows, attached}]`. A socket with no server yet answers `[]` with a note on stderr — that is an empty room, not a fault. |
| `resize -n N --width W --height H` | reflows the pane. Recorded as an `r` event, because `replay` has to reproduce it to rebuild any frame after it. |

## send

`send -n N --type "text" --key Enter [--repeat K --delay S]`

- Text (`-l`) and key names are always separate tmux calls: mixing them in one call makes
  tmux silently drop the keys.
- `--repeat/--delay` issues each repetition as its own call — that is what menu debouncing
  needs (`--key Down --repeat 3 --delay 0.35`).
- `--hex` sends raw bytes, split one byte per `-H` (tmux 3.x drops multi-byte `-H`):
  `--hex 1c` is `C-\`, which has no key name; `--hex 1b5b31333b3275` is kitty Shift+Enter.
- Single-letter program keys are *text*: `--type q`, not `--key q`. `--key` accepts tmux
  key names only and errors with the table if you try something else.
- `--type-file PATH` reads the text from a file (final newline stripped) and sends it
  verbatim. Use it the moment your text carries both quote styles — `'"'"'` gymnastics
  are your *shell's* problem, and the pane runs whatever the shell produced.
- `--type` is passed through **literally**: a backslash stays a backslash, and a `
`
  typed as two characters reaches the program as two characters, not a newline. To press
  Enter, use `--key Enter`; to send a real linefeed byte, use `--hex 0a`.
- The result carries `offset`: the byte position in `raw.log` where this send's output
  begins. That is what `--done`/`--cmd-done` and `--since-send` scope to.
- `sent` summarizes what went in — `{text, keys, calls}` (`calls` = tmux invocations, so
  text and keys are visibly separate). It is a summary, not the pane's echo: to check
  what the pane *received*, read `out` or the next wait's `evidence.screen`.
- `marker` (present when this send *submits* a line) says who supplies its completion
  marker:
  - `shell` — the pane emits OSC 133 on its own; your text went in exactly as typed.
  - `sentinel` — it does not (a theme wrapper owns the prompt: blerust, some
    powerlevel/starship setups), so term-use appended its own
    `; __tu_ec=$?; printf '\033]133;C\007\033]133;D;%s\007' "$__tu_ec"`. The line on
    screen is longer than what you wrote, and `sent.text` shows the whole of it — read
    it back before reasoning about what ran. The printf is the line's last stage, so
    a later command's `$?` is 0; read the code from the verdict, not from `$?`.
  - `paused` + `marker_reason` — the last marker-bearing line has not reported back:
    either a program (a REPL, `ssh`, a password prompt) holds the tty, or the previous
    command is still in flight. The new line goes through verbatim and is not
    narrated; appending shell syntax here would corrupt a program's input or make the
    marker unattributable. Tracking resumes once the outstanding marker arrives, and
    `--done`/`--cmd-done` refuse this send (it has no marker to answer). The result's
    `marker_hint` names the channel to use instead.
  - `skipped` + `marker_reason` — the suffix would have changed what runs: the target
    shell's own parser rejects the line term-use would type (`while true`, `case x in`,
    `function f()`, a background `&`, a dangling operator, a heredoc, a trailing
    comment, an unbalanced quote are examples, not the list), or the shell does not
    speak `$?` (fish, csh), or its command word is not one term-use narrates (a bash
    binary under another name is still skipped). That line has no fact channel: anchor
    a `--until 'regex' --since-send`, or print a sentinel yourself where the command's
    own text allows it. `--done`/`--cmd-done` refuse it rather than lend it an earlier
    marker, and the result carries `marker_hint` with the next move.
  A send that does not submit (text, no Enter) reports no `marker` field at all, and so
  does a bare Enter with nothing pending — there is no line to mark. When Enter arrives
  as its own send, the pending line is read back from the recording and marked then, so
  the documented type-then-Enter split still gets a fact. `TERM_USE_SENTINEL=0` turns the
  append off entirely.
- Warns `tty-echo-broken` when your text produced no echo bytes (the program mangled the
  tty — run `fix-tty`).

## When the channel refuses

term-use narrates one complete command in a shell whose `$?` it can read and whose own
parser accepts the line *with the suffix* (`shell -n`). That is a property of the shape,
not a list of supported shells: when the shape does not fit, anchor the work yourself.

| refusal | what is still true | do |
|---|---|---|
| `marker: skipped` | the line runs exactly as typed; no marker | `--done`/`--cmd-done` refuse it; submit a marker you own, in the target's language, in the same send — or `wait --until 'regex' --since-send` |
| `marker: paused` | the tty belongs to a program or a previous command; your input goes through verbatim; no marker was attached | `--done`/`--cmd-done` refuse it; drive the program with its own prompts/output; `--shell-ready` after it exits; never force a marker into it |
| `no-shell-integration` | no done marker can arrive from the shell, and this server has no native status | start a shell term-use can inject into; own a marker; `--until`; `--exit` when the program is the pane |
| `shell-protocol-unavailable` | the foreground is not the shell `start` launched, so its markers cannot answer | `--until --since-send`, `out`, or your own marker; on tmux 3.8 the native channel may still answer markers from the far side |
| `unmanaged-pane` | term-use has no recording of this pane — or the address resolves to a pane the records do not describe (an explicit `:win.pane`, or an action command on a bare session whose active window moved) | `screen`, `--until`/`--quiet-ms` (degraded), `--exit`; address the recorded pane, or start your own session for facts. `out`/`trace`/`replay` describe the recording and still answer a bare session |

The shell and program names elsewhere in this reference are examples, not a support
list: the condition is always the same — the fact channel cannot see a completion, so
change channel.

## wait

`wait -n N [--done | --cmd-done | --shell-ready | --exit | --until 're' | --quiet-ms N]
[--expect-code C] [--since-send] [--scrollback N] [--timeout T] [--interval S]`

Defaults: `--timeout 10`, `--interval 0.1`. `--until` is Python `re` with DOTALL|MULTILINE.

| Oracle | Confidence | Proves |
|---|---|---|
| `--done` | the chosen oracle's | the last thing you sent finished; picks the shell protocol (native `tmux-command-status` where the baseline has evidence, otherwise `osc133`), then `shell-ready-probe` → `pane-exit` → `quiet-sampling`, and reports what actually answered in `via`/`channel`. A send whose marker was `paused`/`skipped`/`none` is refused (`shell-protocol-unavailable`) — an earlier command's marker cannot answer it |
| `--cmd-done` | fact | an OSC 133 `D` marker arrived after your last send (also reads VSCode's OSC 633) **and is bound to that send** — see Attribution below. It refuses a markerless send for the same reason |
| `--shell-ready` | fact | the shell answers a `true` probe — i.e. it holds the terminal again |
| `--exit` | fact | the pane died (needs `remain-on-exit`); signal name normalized |
| `--until 're'` | inference | text is on the screen. `echo_suspect: true` + downgrade to `heuristic` when it is your own typed text |
| `--since-send` | — | scopes `--until` to output produced after the last send, so stale text cannot satisfy it |
| `--quiet-ms N` | heuristic | the display stopped changing (dual gate: last *cell-reaching* write + screen
snapshot stability; unclosed CSI 2026 or an alt-screen handover (`?1049h`) forces
instability). Bytes that paint nothing — window-title frames (`ESC ]0;…`), OSC 133
markers, clipboard and notification reports — do **not** hold the gate open, so a
program that animates only its title bar settles while it runs. While a program
holds the pane the verdict carries `program_running: true`, plus `silent_unproven: true`
when nothing that paints the screen has arrived since your send: **a settle is about
pixels, not completion** |
| `--scrollback N` | — | widens the `--until` search into history only; refused with other oracles |

**Attribution — why a marker inside your window is not yet an answer.** `send` draws the
window at the recording's current size, and the recorder lags the pane: inject while the
shell is busy and the *previous* command's `C` and `D` both land inside it. Two rules
hold the claim together:

- **send side:** a new marker-bearing line is only narrated when the previous
  marker-bearing send has reported its completion (`last_marker_offset` + its `D`, or,
  for a shell marker on tmux 3.8, native start/end evidence). Otherwise the line is
  `paused`, carries no marker, and `--done`/`--cmd-done` refuse it.
- **wait side:** for a marker the shell itself emitted, term-use accepts it only when
  the echo of the typed text sits between the offset and the authorizing `C`, or, for a
  keys-only send, the recording was quiet when `send` measured it. The echo position is
  `(event, offset inside the event)`: a frozen recorder can deliver the previous
  command's markers and the echo as one event, and only the in-event offset orders
  them.

When neither holds the wait keeps failing and the timeout evidence carries
`"degraded": "unattributed-marker"`. What to do: wait for the previous command before
sending the next one, type the command with `--type` instead of bare keys, use
`--shell-ready` to ask the shell directly, or run the pane on tmux 3.8+, where the
native channel binds a completion to a *start time* and needs no echo binding.

A sentinel send is different: its marker is term-use's own, so when the recording
cannot supply it and the shell is known to emit OSC 133 of its own, tmux's status is
not used — it may be the shell's later `D;0`, which reads the sentinel's printf as
`$?`=0. That timeout carries `"degraded": "native-ambiguous"`; wait for the
recording to catch up and run the wait again, or use `--until --since-send`.

**When the shell reports nothing at all**, `send` appends a completion sentinel of its own
(see `## send`) so the exit code still arrives as a `fact` — and the met verdict then says
in its `note` that the marker was synthesized by term-use, not confessed by the shell. That
provenance matters: the code is the `$?` of the line's last stage, read immediately, and it
is not evidence that the target has shell integration.

Verdict shape: `{verdict, confidence, conditions[], evidence{screen,…}, context, via,
channel, advice?, exit_code?, note?}`. Composite verdicts take the weakest member's
confidence, while each entry in `conditions[]` reports what *it* answered — a timed-out
AND shows the child that did match (with its `exit_code`) beside the one that never did.
A `note` on a met `--done`/`--cmd-done` means your line was compound (`;`, `&&`, `||`,
`|`, `&`): the one `exit_code` describes only part of it, and the note quotes **which
command** the number came from (your own last segment, echoed back) and what a zero means
for that operator — `&&` zero = every stage passed, `||` zero = an earlier stage already
succeeded and the last one may never have run. A `;` inside quotes is data and never
triggers it. `--expect-code` is refused, not ignored, when the oracle `--done` fell back to
has no code to read (quiet sampling) or is the `true` probe.
Unmanaged panes (no `state.json`): `--until`/`--quiet-ms` run screen-only with
`"degraded": "unmanaged"`, capped at `heuristic`; `--cmd-done`/`--shell-ready`/`--since-send`/
`out` refuse with `unmanaged-pane`; `--exit` works and reports `"degraded":
"status-unavailable"` with `exit_code: null` (so do not pass `--expect-code`).

## screen

`screen -n N [--meta] [-N] [-J] [--grid] [--runs] [--grep [--attr A] [--fg C] [--bg C]]
[--find TEXT] [--element-at X Y] [--scrollback N]`

- default: the screen as text. `--text` is the explicit spelling of the default (accepted
  for symmetry); `-N` keeps the trailing cells tmux would strip — that is what preserves a
  painted background and column alignment; `-J` joins the lines tmux wrapped at the right
  edge back into one logical line.
- `--grid`: every cell as an array (`--json`-shaped), `--runs`: the SGR attribute runs
  (token-cheap), `--grep`: filtered runs — `--attr` names are bold, dim, italic, underline,
  blink, reverse, strikethrough; `--fg`/`--bg` take color names, 256-palette indexes or
  `#rrggbb`.
- `--find TEXT`: literal text → 1-based, click-ready `{text, x, y}` in **screen columns**
  (double-width glyphs counted correctly, so `click` lands where you meant).
- `--element-at X Y`: one cell's char/fg/bg/attrs.
- `--meta`: pane status JSON — cursor, `alternate_on`, `pane_in_mode`, `history_size`,
  death, size, `pane_current_command`, `pane_current_path`, plus `blank`, `cells`,
  `nonblank_rows`, `context`, `last_output_age_s`, `unexecuted_input`, `managed`.
  It is a different question from the text flags, so combining them is refused rather than
  half-honoured.
  `blank` means no non-space glyph *and* no attributed cell. `unexecuted_input` non-empty
  with a growing `last_output_age_s` means your keystrokes are landing on a line nothing is
  reading — different from a program that is merely quiet.
- On tmux 3.8+ `--meta` additionally carries `pane_command_status` / `_running` /
  `_start_time` / `_end_time` / `_duration`, `pane_last_prompt_time`,
  `pane_output_generation` — see `references/tmux-versions.md`.

## out / replay / trace

- `out -n N [--all] [--max-bytes K] [--json]` — the pane's own byte stream since your last
  send (or the whole session), head+tail with `interior bytes omitted` marked. `--json`
  states `since_offset`, where the window begins, so "the row is not in here" can be told
  apart from "my window starts after it" — those are different findings and only one of
  them is worth re-running a non-idempotent step for. It is the **unwrapped byte stream**: a long typed
  command appears whole, whereas a wait's `evidence.screen` shows it broken at the pane
  width. Both are the pane's truth at different layers — neither rewrites the other.
  *pane*, so it contains your echoed input line and the prompt that follows; a trailing
  prompt with nothing after it means "the program said nothing", which is a result.
  `--json` adds `bytes/truncated/omitted_bytes/redrawn/full_bytes`. When `redrawn` is true
  the program repositions text with cursor escapes and this linear text is not the frame it
  showed — get the frame with `replay`.
- `replay -n N --at 3.5|offset:N|last-send [--format text|json|png] [-o frame.png]
  [--no-cursor] [--timeout T]` — the screen at an earlier moment, rebuilt by feeding
  `raw.log` back through tmux itself in a throwaway pane (no second emulator to disagree
  with). `json` gives `{grid, blank, cells, text, cursor, alternate_on, context}`; `png`
  draws the reconstructed cursor unless `--no-cursor`. `-o` applies to `--format png` only.
- `trace -n N [--format json]` — the timeline. `text` = a header (`# v2 <WxH> cmd=…`) then
  `[ elapsed ] code NB 'preview'` per event: `o` pane output, `i` your injections, `r`
  resizes, `m` sync points (`start`, `wait-met`). `json` = one NDJSON line per event,
  `[elapsed, code, data]` — parse it line by line, it is not one JSON document. Use `out`
  for *what it said*; `trace` is for *when*.

## screenshot / clear / mouse / fix-tty

- `screenshot -n N [-o shot.png] [--font-size 16] [--no-cursor]` — renders the pane to PNG
  (the path must end in `.png`). PNG only: a terminal screen uses few colors, so an
  exact-count palette is lossless and roughly 7x smaller than what JPEG produced, and JPEG
  was never palette. Draws the pane's real geometry (never cropped to content), outlines
  the cursor, reports `glyphs.missing`/`missing_samples` instead of emitting silent tofu,
  and reports `font`/`font_source` (`bundled` when only the shipped face could answer).
  `glyphs` says how much of the picture is trustworthy: `vector` = box-drawing, blocks,
  braille, powerline and arrows drawn as geometry (no font needed); `primary` = the chosen
  mono; `fallback` = another face opened *because this screen lacked coverage*, listed in
  `fallback_fonts`, ranked by the codepoints actually missing (one face per family, so a
  directory of seven `unifont*` cuts cannot crowd out the CJK face); `missing` = reported
  boxes, not silent ones. `TERM_USE_FG`/`TERM_USE_BG` set the defaults,
  `TERM_USE_FONT`/`TERM_USE_FONT_DIRS` the search.
  `TERM_USE_DISABLE_IMAGE=1` denies the image channel (`screenshot` and
  `replay --format png`) with `pillow-missing`.
- `clear -n N [--history-only]` — reclaims the screen and scrollback before the next
  capture carries them again.
- `mouse-detect -n N` → `{enabled, sgr, normal, motion, any_motion}`; `click -n N X Y`
  sends an SGR click at **1-based** coords (curses programs print 0-based).
- `fix-tty -n N` — C-u then `stty sane`, for a program that left the tty without echo.

## Context accounting

Every observation carries `context`: `bytes` (exact), `est_tokens` + `method` (`bytes/4`
for text, `pixels/750` for an image), `budget`, `over_budget`. The estimate is a rule of
thumb, not a meter: box-drawing, CJK and the SGR grid JSON cost noticeably more per byte
than prose. Over budget the tool says so **on stderr** (so stdout stays pipeable) and names
the fix — `clear -n N`, a narrower channel, or a fresh session. `TERM_USE_CONTEXT_BUDGET`
retunes the threshold; images are the most expensive thing you can accumulate.
