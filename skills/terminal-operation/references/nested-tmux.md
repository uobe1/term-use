# Nested tmux and unmanaged panes

A tmux client running *inside* a term-use pane is drivable directly, with three gotchas.

1. **`$TMUX` blocks the nested client.** tmux refuses to start ("sessions should be nested
   with care, unset $TMUX to force"). Run it as
   `send --type 'unset TMUX; tmux new-session -s inner' --key Enter`.
2. **Address inner panes by socket name.** The default socket's name is literally
   `default`, so `screen -n default:inner:0.0 --socket <outer>` works, while a bare
   `-n inner` does not: it looks for a term-use-managed session and fails with
   `session-missing`. The inner server's own socket name is what you address, not the outer.
3. **Unmanaged panes degrade, they do not overclaim.** For a pane term-use did not start
   (no `state.json`):
   - `send`, `screen`, `clear` work unchanged.
   - `--until` and `--quiet-ms` work with degraded confidence — capped at `heuristic`,
     `"degraded": "unmanaged"` in the verdict, and raw-stream echo detection off, so anchor
     on output that is *not* a substring of what you sent.
   - `--cmd-done`, `--shell-ready`, `--since-send` and `out` refuse with `unmanaged-pane`:
     they need term-use's recording of that pane, which does not exist. Start your own
     session (`start -n NAME --cmd bash`) if the job needs fact-level waits.
   - `--exit` is read-only and still works, even when the session collapses with the pane:
     met at `fact` with `"degraded": "status-unavailable"` and `exit_code: null`, so do not
     pass `--expect-code` there.
   - Never create record state as a side effect of touching an unmanaged pane.

## Shell-protocol waits through a nested or remote terminal

When the foreground process is not the shell `start` launched, the local shell is
blocked inside it and its markers cannot answer for what you type into that program.
`ssh`/`mosh`/`telnet`, a nested `tmux`/`screen`/`dtach`/`abduco`, container exec,
`sudo -s`/`su` and a language REPL are examples of the same shape, not the list: what
matters is that the tty now belongs to another program. The remote/REPL output arrives
as ordinary bytes to term-use's recording. This shape raises
`shell-protocol-unavailable` rather than timing out, and the advice is to change
channel (`--until 'regex' --since-send`, or print your own sentinel like
`printf 'DONE%s\n' "$?"`, in that program's own language).

It does **not** mean "send the program's quit key": the shell behind ssh may be idle and
healthy, and a stray interrupt is how a working session gets wrecked. On tmux 3.8+ the
native channel can see markers forwarded from the far side, so a remote shell that keeps
its own integration regains fact-level `--done` there — see
`references/tmux-versions.md`.
