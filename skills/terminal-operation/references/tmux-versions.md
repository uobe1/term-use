# tmux versions, native command status, and the environment knobs

Only load this when the fact-level waits are behaving oddly.

`screen --meta` says whether the server can answer at all: the `pane_command_*` keys are
**absent** (not empty) when it has no command tracking, because tmux expands unknown formats
to empty and term-use drops the group rather than reporting a value that would read as "no
command has finished".

## What tmux 3.8+ answers that term-use reads

tmux parses OSC 133 itself and keeps per-pane state:

| Key | Meaning |
|---|---|
| `pane_command_status` | exit code of the last completed command, from the `D;<code>` marker |
| `pane_command_running` | 1 while a command is in flight |
| `pane_command_start_time` / `pane_command_end_time` | 1-second granularity timestamps |
| `pane_command_duration` | seconds the last command took |
| `pane_last_prompt_time` | when a prompt (`A` marker) last arrived |
| `pane_last_output_time` | when the pane last produced output |
| `pane_output_generation` | monotonic output counter |

`wait --done`/`--cmd-done` consult this **before** replaying the recording, and the verdict
says which channel answered (`via`/`channel` = `osc133` or `tmux-command-status`). It is a
second fact source, and the one that survives the three shapes where term-use's own
recording cannot: a `pipe-pane` that stopped, a prompt framework that took `PROMPT_COMMAND`
back, and markers forwarded from the far side of ssh. Where the server has no command
tracking, `via: osc133` is *all this build can do* — it does not mean the oracle was chosen
over an alternative.

On a server that has it, the native channel is treated as an oracle, not as a hint:

- `wait --done` chooses `tmux-command-status` when the baseline carries a status, a
  running command, a prompt time, or this send's own sentinel; `via` then names the
  native channel even when `state.shell_integration` is false.
- `--cmd-done`/`--shell-ready` accept the group as proof that a fact channel exists even
  when `raw.log` carries no markers (a detached recorder, or markers that only reached
  tmux). They no longer refuse with `no-shell-integration` on such a pane.
- A timeout's `in_flight` ORs `pane_command_running`, so a blind recording cannot make a
  running command look finished. `E` is not a completion — VSCode's `633;E` carries the
  command line; only `D` ends a command.
- The send-side attribution check accepts native start/end times as proof that the
  previous *shell-marker* command finished when the recorder is blind; otherwise every
  later line would be `paused` after a detached pipe. A sentinel marker is not proved
  this way: tmux sees its `C`/`D` only after the command, and the code cannot be tied
  to the line that emitted it; its `D` must reach the recording.
- `screen --meta` uses `pane_last_output_time` for `last_output_age_s` when present, and
  says `last_output_age_source: tmux`; a stopped recorder therefore does not make a
  live pane look silent.
- `--quiet-ms` can still settle when the recorder never wrote an o-event: on 3.8 the
  screen-stability gate answers and the verdict carries `degraded: recorder-blind`
  instead of pretending the raw-stream gate ran.

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
- Nothing an agent can do turns the group on. If the keys are absent, do not go looking for
  a liveness signal on the screen either: `last_output_age_s` only measures silence. The
  portable fact is term-use's own marker stream — `C` with no `D` since your send — which
  the tool reports as `program_running` on a quiet settle and `in_flight` on a `--done`
  timeout.

## tmux behaviors the tool relies on

| Server | what holds |
|---|---|
| with command tracking | the keys above, plus the `pane-command-started` / `-finished` / `pane-shell-prompt` hooks |
| without it | everything works through term-use's own `raw.log` scan, which is a fallback and not an equal: a completion has to be bound to your send before it is called `fact` |

Two things that bite on any server:

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

A screenshot costs more context than the text channels and answers fewer questions about a
terminal, so it belongs to exactly one question — *how is this frame laid out*.
