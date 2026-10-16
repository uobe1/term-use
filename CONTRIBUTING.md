# Contributing to term-use

Thanks for your interest in contributing! Bug reports, documentation and code are all
welcome. The workflow is **issues + pull requests**: direct pushes to `main` are
blocked by a branch ruleset, so all changes — including small ones — arrive through PRs.

## Reporting bugs

Open a [GitHub issue](https://github.com/uobe1/term-use/issues) and include:

1. **The structured error JSON** — term-use prints every failure as single-line JSON
   with an evidence snapshot attached. Paste it verbatim; it is designed for this.
2. `tmux -V`, `python3 --version`, OS/terminal.
3. A minimal reproduction: the exact `start` / `send` / `wait` / `screen` commands.
4. If relevant, a `trace -n NAME` excerpt or the session's `raw.log` tail
   (**sanitize first** — recordings can contain anything your pane displayed).

Feature requests are also issues — please describe the scenario you are driving
(program, what you need to observe) rather than the API you imagine.

## Version numbering

Format `vX.Y[.Z]aN` / `vX.Y[.Z]bN` / `vX.Y[.Z]`, where `X` is the major version and
`Y` the minor one. The suffix says what kind of build it is:

| Suffix | Meaning | Release posture |
|---|---|---|
| `aN` | **alpha** — early test line (like an Intel ES chip): rough, changes freely, expected to break | never tagged as a GitHub release |
| `bN` | **beta** — final test line (Intel QS): feature-complete candidate, only fixes | tagged when promoted |
| none | **formal** — the released version | tagged, GitHub release |

`N` counts iterations within that line and resets when `X.Y` advances
(`2.0.0a9` → `2.1.0a1`). `pyproject.toml` carries the same number without the `v`
(`2.0.0a3`), which stays PEP 440-valid so `uv`/`pip` sort it correctly.

**Three lines, three branches** — alpha, beta, and formal advance independently; they
are *not* force-synced. Routing:

- New PRs land on the **alpha** line by default: it is where the most breakage is
  expected, so unbaked changes go there first.
- Work that has been **verified** in real use graduates to the **beta** line.
- Only what has **survived beta** becomes a formal `feature` release.

Cherry-pick forward (alpha → beta → formal); never rewrite a line the other way.

## Development workflow

0. **Local setup** — the source tree runs without any install step:
   ```bash
   PYTHONPATH=src python3 -m termuse --help      # run from a source checkout
   uv tool install -e .                            # or get the `term-use` command
   ```
   Unit tests and E2E scripts manage `sys.path` / `PYTHONPATH` themselves; E2E
   isolation (socket name, `XDG_STATE_HOME`) is automatic — never point them at
   your real `~/.local/state/term-use/`.
1. Fork / create a feature branch from `main`.
2. Write the **failing E2E test first** (see discipline below).
3. Implement the minimal change until it goes green.
4. Run the full regression suite.
5. Open a pull request referencing the issue.

### e2e-first discipline

- E2E scripts drive **real tmux and real programs through the CLI only** — no mocks,
  no bypassing the CLI, no editing files the target program owns. Helper programs
  for scenarios that need a scripted target (e.g. a deterministic debouncing menu)
  live in `tests/fixtures/`.
- Follow the self-isolation pattern of `tests/e2e/*.sh`: unique tmux socket name per
  run, `XDG_STATE_HOME` pointed at a `mktemp -d` directory, `trap`-based cleanup,
  and always `stop` your sessions.
- Unit tests live in `tests/unit/test_*.py`, are pure asserts (pytest optional), and
  insert the repo root into `sys.path` themselves.

### Hard guarantees your change must preserve

- **Confidence taxonomy**: `fact` (OSC 133 / pane death) > `inference` (screen regex)
  > `heuristic` (quiet sampling). Composite wait verdicts take the weakest member's
  confidence. Never let a heuristic masquerade as fact.
- **Honest degradation**: unmanaged panes (nested tmux — no `state.json`/`raw.log`)
  get screen-only waits with confidence capped at `heuristic` and a `degraded`
  marker in the verdict; shell-protocol facts refuse with `unmanaged-pane`. Never
  silently overclaim evidence that cannot be gathered, and never create record
  state as a side effect of touching an unmanaged pane.
- **Injection red lines**: `-l` text and key names are never mixed in one `send-keys`
  call; `--hex` is split one byte per `-H`.
- **Structured errors**: every failure surfaces as a `TDError` with a code from the
  fixed table in `termuse/errors.py`, as single-line JSON on stderr with exit code 1.
  Timeouts return an evidence snapshot and never kill the target.
- **Atomic state**: `state.json` is always written via tmp + rename;
  `last_send_offset` is a byte offset into `raw.log`, never a timestamp.
- **Stateless client**: no resident daemon, no in-memory state across invocations.

### tmux compatibility

tmux is the only hard dependency, and the tool leans on its behavior details
(`pipe-pane`, `remain-on-exit`, `capture-pane -e/-N/-J/-S`, `display-message -p` formats,
`send-keys` semantics), so the supported line is stated rather than implied:

| tmux | what holds |
|---|---|
| 3.2a – 3.7c | the supported baseline; developed and regression-tested on **3.7c** |
| 3.7 / 3.7a / 3.7b / 3.7c | no drift that matters here: `input_osc_133()` is byte-identical across the patch line, and `capture-pane -F`'s flag vocabulary (`D H O P W X`) is the same. `wait-for` has **no** `-t` timeout and no way to list waiters |
| 3.8 | adds `pane_command_status` / `_running` / `_duration` / `_start_time` and the `pane-command-started` / `pane-command-finished` / `pane-shell-prompt` hooks, and fixes the `wait-for` client leak (tmux #5614) |

Two consequences that have already cost someone a wrong design:

- **tmux ≤ 3.7c cannot answer "did the command finish".** Its OSC 133 parser handles only `A`
  and `C` and discards the exit code in `D;<code>`, so `#{pane_command_status}` is not a fact
  oracle there — it expands to empty *because the format does not exist*, and tmux expands
  every unknown format to empty. Any code that does use those formats must version-gate and cap
  older servers at `heuristic`.
- **Never trust a zero exit code from tmux.** A client that cannot reach its server can print
  `access not allowed` / `couldn't create directory … (Not a directory)` and still exit 0
  (measured on 3.7c). `tmuxio.ok()` — exit 0 *and* clean stderr — is the only success test.

Version-dependent behavior we work around rather than assume: `send-keys -H` drops multi-byte
arguments on 3.x (the CLI splits one byte per `-H`), and `#{pane_dead_signal}` answers a bare
number on some builds ("9") and a name on others ("KILL") — normalized by `tmuxio.signal_name()`.

When you add an assumption about tmux, add the e2e assertion that pins it for the versions you
claim, and say which line it does *not* hold for.

### Dependencies

Python 3.14 **stdlib only**; tmux is the only external hard dependency; Pillow stays
optional (screenshots only). Do not add hard dependencies without discussing it in an
issue first.

### Real-world pitfalls → update the skill

`skills/terminal-operation/SKILL.md` ("Common Mistakes") encodes
hard-won behavioral findings (echo-anchored waits, spinner/quiet resonance, menu
arrow debounce, kitty key encodings, …). If you fix a real-world pitfall, update the
skill in the same PR so agents benefit immediately.

## Commit messages

Follow the existing style:

```
feat(v2): ...
fix(v2): ...
docs(v2): ...
```

One logical change per commit; the commit message should say *why*, the diff says *what*.

## Pull request checklist

- [ ] Linked issue (or short scenario description)
- [ ] E2E test added first and now green; full `tests/e2e/` regression passes
- [ ] Unit tests updated/added where pure logic changed
- [ ] No new hard dependencies; stdlib only
- [ ] New failure paths return structured `TDError` JSON, not exceptions/raw text
- [ ] `SKILL.md` updated if this fixes an agent-facing pitfall
- [ ] No secrets or personal data in code, logs, or recordings

## Licensing

term-use is licensed under the **GNU General Public License v3.0 or later**
(`GPL-3.0-or-later`), see [LICENSE](LICENSE). By opening a PR you agree that your
contribution is licensed under the same license (GPL-3.0-or-later).
