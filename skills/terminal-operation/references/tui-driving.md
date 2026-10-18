# Driving an unfamiliar interactive program

Read this before driving a program you have not already run. The method, not a table of
pre-known screens: **classify → observe and derive the anchor → find the exit path → drive
in a wait-loop.**

## 1. Classify it — this decides how completion and exit are confirmed

| Shape | Completion | Exit |
|---|---|---|
| Program *is* the pane (`start --cmd vim`, incl. `bash script.sh` / `sh -c '…'`) | its death is observable | `wait --exit` (fact; works for non-bash too) |
| Program inside bash (`send <prog> --key Enter`) | `wait --done` (fact) | the pane always belongs to bash, so `--exit` never fires. "Has it given up the terminal?" is `wait --shell-ready`: it fails with `shell-not-ready` while the program holds the screen — send that program's own quit key, then re-probe. **Do not probe with `pgrep`**: term-use's own recorder is a python3 process, so a process count is never 0 |
| REPL (prompt + eval) | anchor on the prompt, verify results with `out`/`screen -J` | `--shell-ready` after the quit key |
| Full-screen TUI (alt-screen, repaints) | anchors must be derived from observation | after it exits, the alt-screen restores: stale frames can fake-settle a short quiet and re-match old anchors, so re-verify the screen after any program swap and anchor the *new* program's first paint |

While an interactive program holds the tty, `send` answers `marker: paused`: the line
goes through verbatim and no completion marker is appended. That is protection, not a
failure — keep driving the program with its own prompts and output anchors. After it
exits, `--shell-ready` proves the shell is back; `--done` on the line that launched the
program answers when the shell runs the queued marker, not while the program is running.

## 2. Observe, then derive the anchor

Launch, then catch the first paint before you have any anchor: `wait --quiet-ms 700`
(non-animated programs settle fast), or `sleep 1` + `screen` for timer-driven ones. Read
what the program **actually** paints, then wait on that.

- Good anchors, in rough strength order: dialog text the program prints itself; status-line
  vocabulary that appears in one phase only; prompt shapes (`^>>>`, `^>$`).
- Bad anchors: substrings of your own input, and text you remember from a *different*
  program's manual or version — prompts of the same program drift between releases. Anchor
  on what this pane painted.
- When an anchor times out, read `evidence.screen` and re-anchor; don't widen the timeout
  first.
- Set `--timeout` from a first run of your own (×3–5 headroom). Cold starts on a phone or a
  loaded box can take tens of seconds before the first paint.

## 3. Find the exit path before you need it

Check `--help`/man for the quit key. Conventions: `q` (pagers), `C-d` (REPLs), `C-x C-c`
(emacs), `:q` (vim), `Escape` ×N (menus), `C-c` (interrupt) — then verify on screen rather
than assuming, and confirm the shell took input back with `wait --shell-ready`.

Double-tap rhythms are program-specific: `--repeat 2 --delay 0.3` sends the pair as two
independent calls in one invocation. Measure which rhythm works and err short, because each
CLI invocation's startup inflates the gap.

## 4. Drive in a wait-loop

One action → `wait` for its observed effect → next action. When nothing new is waitable
(input produces no fresh output, answers land before a streaming phase can be caught), stop
burning waits and poll `screen` for evidence instead — a short `sleep`-paced read loop is
honest as long as you say what you read it from.

## Program-type specifics

- **Menus / pickers**: repaint on a timer, so they are quiet-hostile — `sleep 0.5-1` +
  `screen`. Arrows debounce: one key per call ~0.35s apart, or `--repeat N --delay 0.35`.
  To select item N: `screen --grep --attr reverse` to read the highlight → send the arrows
  → **re-run the grep** to confirm the highlight landed where you meant → then Enter.
  Attribute channels do not cover every program: `less`'s search highlight is not
  `reverse`, so verify pager state by plain text match.
- **Live monitors (top/htop style)**: repaint on a timer; one frame can be mid-repaint and
  short-lived processes vanish between refreshes. Read two frames ~2s apart and answer only
  if they agree; answer from the program's own sorted rows, not the aggregate header
  (the header can disagree with rows that are still usable).
- **REPL block input**: any colon-opening statement enters the continuation prompt (`...`)
  and the block needs ONE extra empty Enter to close, otherwise your next line is
  swallowed. Prefer colon-free one-liners; for real blocks send each line separately and
  anchor on the block's *result*.
- **Pagers**: the search term stays on screen after the match, so it is a stale anchor; and
  `q` on a prompt you did not mean to answer can quit the pager mid-search.
- **Spinners / animations**: an animated line changes every ~150ms, but a slow device can
  sample identical frames exactly at that period (frame-cycle resonance). `--quiet-ms` also
  gates on the raw stream's last-write time (25% margin): pick 2-3x the observed frame
  period, prefer a done-marker, and do not trust a large quiet value across a phase where
  the program may only *start* thinking after your input.
- **Key encoding**: `C-\` has no tmux key name (`--hex 1c`); kitty-protocol Shift+Enter is
  `--hex 1b5b31333b3275`.
