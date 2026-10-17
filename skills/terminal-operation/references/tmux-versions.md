# tmux versions, native command status, and the environment knobs

Only load this when you are on tmux 3.8+ or when the fact-level waits are behaving oddly.
Run `tmux -V` to find out where you are; `screen --meta` also tells you by itself — the
`pane_command_*` keys are **absent** (not empty) on 3.2a–3.7c, because tmux expands unknown
formats to empty and term-use drops the group rather than reporting a value that would read
as "no command has finished".

## What tmux 3.8+ answers that term-use reads

tmux parses OSC 133 itself and keeps per-pane state:

| Key | Meaning |
|---|---|
| `pane_command_status` | exit code of the last completed command, from the `D;<code>` marker |
| `pane_command_running` | 1 while a command is in flight |
| `pane_command_start_time` / `pane_command_end_time` | 1-second granularity timestamps |
| `pane_command_duration` | seconds the last command took |
| `pane_last_prompt_time` | when a prompt (`A` marker) last arrived |
| `pane_output_generation` | monotonic output counter |

`wait --done`/`--cmd-done` consult this **before** replaying the recording, and the verdict
says which channel answered (`via`/`channel` = `osc133` or `tmux-command-status`). It is a
second fact source, and the one that survives the three shapes where term-use's own
recording cannot: a `pipe-pane` that stopped, a prompt framework that took `PROMPT_COMMAND`
back, and markers forwarded from the far side of ssh. On tmux 3.2a–3.7c there is no second
server, so `via: osc133` is *all this build can do* — it does not mean the oracle was
chosen over an alternative. Tell the two apart with `tmux -V`, not with the verdict.

### Limits, stated because they change what you can claim

- The timestamps have one-second granularity, so a completion is proved by `end_time`
  advancing past the baseline stamped at send time, by a changed exit code, or by having
  seen `running=1` — and a report whose `pane_command_start_time` is the baseline
  command's is refused, because that is the *previous* command finishing while your line
  sat in the tty buffer. When none of those holds, term-use falls back to the byte scan,
  which is sub-second but must bind the marker to your send (see `channels.md`,
  Attribution).
- **This is why 3.8+ is the version to run on.** Below it, a keys-only send into a
  recorder that cannot drain has no honest answer left, and the wait degrades instead of
  claiming a code. The native channel is not one more source among equals: it is the one
  that proves *which* command a completion belongs to.
- **`pane_command_running: true` does not mean "your command is still going."** `PS0` emits
  `C` and `PROMPT_COMMAND` emits `D`, so a framework that swallowed `D` sticks the flag at
  1 forever — and a shell blocked on `read` or a password prompt leaves exactly the same
  mark. Only `evidence.screen` separates them: an idle prompt on screen means the hooks
  died (change channel), a program asking for something means send that input. Never send a
  quit key on the strength of the flag alone.
- Nothing an agent can do turns the group on below 3.8. If the keys are absent, do not
  go looking for a liveness signal on the screen either: `pane_current_command` is a
  weak hint (measured on 3.7c: it reported `bash` 1.2 seconds into a `sleep 5`), and
  `last_output_age_s` only measures silence. The version-independent fact is term-use's
  own marker stream — `C` with no `D` since your send — which the tool reports as
  `program_running` on a quiet settle and `in_flight` on a `--done` timeout.

## tmux behavior the tool relies on, by version

| tmux | what holds |
|---|---|
| 3.2a – 3.7c | everything works through term-use's own `raw.log` scan. The `pane_command_*` group does not exist (≤3.7c parses OSC 133 `A`/`C` only and discards `D;<code>`) |
| 3.7 / 3.7a / 3.7b / 3.7c | no difference that matters: `input_osc_133()` is byte-identical and `capture-pane -F`'s flags (`D H O P W X`) are unchanged. `wait-for` has no `-t` and cannot list waiters |
| 3.8+ | the group above, plus the `pane-command-started` / `-finished` / `pane-shell-prompt` hooks, and the `wait-for` client leak fix (#5614) |

Two measured traps, whatever your version:

- **tmux exits 0 while failing.** `access not allowed`, `couldn't create directory … (Not a
  directory)` and friends can come back with rc 0. term-use treats exit 0 *with* stderr as
  failure and reports it structurally — do not read tmux's silence as success in your own
  shell code either.
- **Build variance.** `send-keys -H` drops multi-byte arguments (the CLI splits one byte per
  `-H`); `#{pane_dead_signal}` answers a bare number on some builds ("9") and a name on
  others ("KILL").

## Environment knobs

| Variable | Effect |
|---|---|
| `XDG_STATE_HOME` | where each session's `raw.log` + `state.json` live. Must be the **same value** for `start` and every later command, or you get `session-missing` with a hint about exactly this |
| `TMUX_TMPDIR` | where tmux looks for its sockets. An inherited value pointing somewhere that does not exist here (e.g. another device's Termux prefix on a non-Android box) makes tmux fail with rc 0 and a stderr line — which is why `--socket NAME` plus a directory you own is the default advice |
| `TERM_USE_CONTEXT_BUDGET` | retunes the over-budget warning threshold (default 2000 tokens of est) |
| `TERM_USE_SENTINEL` | `0` stops `send` appending term-use's completion sentinel to a line in a shell that emits no OSC 133 of its own. With it off, such a pane has no fact channel at all: `--done` falls back to sampling and `--cmd-done`/`--shell-ready` refuse |
| `TERM_USE_DISABLE_IMAGE` | `1` denies the image channel (`screenshot`, `replay --format png`) with `pillow-missing`, for text-only testing |
| `TERM_USE_FG` / `TERM_USE_BG` | default colors of the rendered picture |
| `TERM_USE_FONT` | one face, by file |
| `TERM_USE_FONT_DIRS` | **replaces** the search list entirely. If nothing in it qualifies, the face bundled with term-use draws the picture and the run says `font_source: bundled` on stderr — an explicit list changes the search, it does not remove the last resort |
| `TMPDIR` | use it instead of `/tmp`, which is unwritable in some sandboxes |

## Why the text channels are the default

Reported by the OSWorld benchmark — **their measurement, not ours**: agents given text/a11y
observations scored 12.24% against 5.26% for screenshots, and stacking image history in the
context is what failed. Treat that as direction, not as a number for your model. What this
tool claims on its own is narrower and checkable: a screenshot costs more context than the
text channels and answers fewer questions about a terminal, so it belongs to exactly one
question — *how is this frame laid out*.
