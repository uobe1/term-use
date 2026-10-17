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
| `start -n N --cmd bash --width W --height H` | creates the session and its recording. bash/zsh get OSC 133 hooks injected; fish ≥4 emits them itself; plain `sh`/`dash` cannot (no pre-exec hook). The target is gated until the recorder is attached, so a program's first bytes are never lost. |
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
- Warns `tty-echo-broken` when your text produced no echo bytes (the program mangled the
  tty — run `fix-tty`).

## wait

`wait -n N [--done | --cmd-done | --shell-ready | --exit | --until 're' | --quiet-ms N]
[--expect-code C] [--since-send] [--scrollback N] [--timeout T] [--interval S]`

Defaults: `--timeout 10`, `--interval 0.1`. `--until` is Python `re` with DOTALL|MULTILINE.

| Oracle | Confidence | Proves |
|---|---|---|
| `--done` | the chosen oracle's | the last thing you sent finished; picks `osc133` → `shell-ready-probe` → `pane-exit` → `quiet-sampling` and reports it in `via`/`channel` |
| `--cmd-done` | fact | an OSC 133 `D` marker arrived after your last send (also reads VSCode's OSC 633) **and is bound to that send** — see Attribution below |
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
shell is busy and the *previous* command's `C` and `D` both land inside it. term-use
accepts such a marker only when it can bind it to the send that caused it — the echo of
the text you typed sits between the offset and that `C` (tmux orders a pane's stream, so
bytes from before your injection cannot come after your echo), or, for a keys-only send,
the recording was quiet when `send` measured it. When neither holds the wait keeps
failing and the timeout evidence carries `"degraded": "unattributed-marker"`. What to do:
type the command with `--type` (a typed line carries its own binding) instead of sending
bare keys, use `--shell-ready` to ask the shell directly, or run the pane on tmux 3.8+,
where the native channel binds a completion to a *start time* and needs no binding at all.

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
  apart from "my window starts after it" (an ab8 arm could not tell, and re-verified a
  non-idempotent step three times). It is the **unwrapped byte stream**: a long typed
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
