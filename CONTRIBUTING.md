# Contributing to term-use

Bug reports, documentation and code are all welcome. The workflow is
**issues + pull requests**: direct pushes to `main` are
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
(`2.0.0a5`), which stays PEP 440-valid so `uv`/`pip` sort it correctly.

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
- **A completion is authorized, not merely positioned**: `send` reads its offset from
  the recording's current size while the recorder lags the pane, so the *previous*
  command's `C` and `D` can both land after it. `CmdDone` accepts a `D` only when the
  `C` that opened it is also inside the send's window, and tmux 3.8's native answer is
  rejected while `pane_command_start_time <= baseline` even if `end_time` moved. Position
  alone is not ownership — a met `fact` for the command before yours is the worst thing
  this tool can do.
- **Stateless client**: no resident daemon, no in-memory state across invocations.

### tmux compatibility

tmux is the only hard dependency and the tool leans on its behavior details, so the
supported line is stated, not implied. **Recommended: 3.8+** (native command status,
which is what makes completion facts survive ssh/remote shells and prompt frameworks
that rewrite `PROMPT_COMMAND`). Minimum: 3.2a, on a self-scanned fallback.

| tmux | holds |
|---|---|
| 3.2a – 3.7c | everything works via term-use's own `raw.log` scan. The `pane_command_*` group does **not** exist (≤3.7c parses OSC 133 `A`/`C` only and discards `D;<code>`) — and tmux expands *any* unknown format to empty, so `tmuxio.meta()` drops the group when `pane_command_running` comes back empty. Absence is the version answer; an empty value would be read as "no command has finished", which is a different claim |
| 3.7 / 3.7a / 3.7b / 3.7c | no drift here: `input_osc_133()` is byte-identical, `capture-pane -F` flags (`D H O P W X`) unchanged. `wait-for` has no `-t` and cannot list waiters |
| 3.8 | `pane_command_status` / `_running` / `_start_time` / `_end_time` / `_duration`, `pane_last_prompt_time`, `pane_output_generation`, `pane_command`; hooks `pane-command-started` / `-finished` / `pane-shell-prompt`; fixes the `wait-for` client leak (#5614). term-use reads the group as a second fact channel: `--cmd-done` consults it before replaying the recording, `screen --meta` exposes it, and `shell-protocol-unavailable` carries tmux's own reading in `evidence.native` |

Measured on 3.8, because the shape of that channel decides how it is used: `running`
is 0/1 whenever the group exists (so it is the presence probe), `status` stays empty
until the first `D;<code>` marker reaches the pane, and it is filled by markers from
**any** program in the pane — including a remote shell's, forwarded through ssh. The
timestamps have one-second granularity, so a completion is proved by `end_time`
advancing past the baseline stamped at send time, by a changed code, or by having
seen `running=1`; when none of those hold, the byte scan answers instead.

`running=1` is **not** the answer to "is my command still going", and a test found
that the hard way: a prompt framework that takes `PROMPT_COMMAND` back kills `D`
while `PS0` keeps emitting `C`, and a shell blocked on `read` does the same — both
stick the flag at 1 forever. Only the screen tells those apart (idle prompt vs a
program asking for input), which is why `shell-protocol-unavailable` reports the
native reading as evidence and names both readings in its hint instead of choosing
one for the agent.

Two traps, both measured:

- **tmux exits 0 while failing** (`access not allowed`, `couldn't create directory …`):
  `tmuxio.ok()` — exit 0 *and* clean stderr — is the only success test.
- **Build variance**: `send-keys -H` drops multi-byte args (CLI splits one byte per `-H`);
  `#{pane_dead_signal}` answers "9" on some builds, "KILL" on others (`tmuxio.signal_name()`).

New tmux behavior assumptions ship with the e2e assertion that pins them, and say which
version line they do *not* hold for.

### Dependencies

Python 3.14 **stdlib only**; tmux is the only external hard dependency; Pillow stays
optional (screenshots only). Do not add hard dependencies without discussing it in an
issue first.

### Real-world pitfalls → update the skill

`skills/terminal-operation/SKILL.md` ("Traps", "Failure codes") encodes
hard-won behavioral findings (echo-anchored waits, spinner/quiet resonance, menu
arrow debounce, `$?` expanded before tmux sees it, kitty key encodings, …). If you fix
a real-world pitfall, update the skill in the same PR so agents benefit immediately.

The skill is **two-layer and priced per token**: the core `SKILL.md` is what every
session loads, so it stays short (~150 lines) and carries only what an agent needs
before acting; `references/*.md` are loaded at the moment the core names them. New
detail goes into a reference unless it changes a decision; rationale and measurements go
into the repo docs, not the skill body. Do not state a rule twice.

Is a skill revision actually better? `tests/blind/ab_skill.sh` answers that with agents
rather than opinion — objective verdict from the fixture's end state, weighted confusion
self-report, plus wall clock / tokens / `arm_metrics.py`'s transcript-measured cost
(observations, tool-side bytes, peak context occupancy). Baselines are pinned by
`OLD_SKILL_REF`; runs are archived under `~/evidence/term-use/ab/` with an `INDEX.md`
entry, because an unarchived experiment is a story.

Four rules that came out of running it, not from designing it:

- **k>=2 waves, and quote the mean.** In the a6-vs-a5 round, wave 1 measured −33% wall
  clock and −23% tokens; wave 2 measured −7% and **+4%**. The spread between waves is
  bigger than several of the deltas, so a single wave's percentage is noise.
- **`archive` takes the arm's state dirs.** An arm's `$XDG_STATE_HOME` is a mktemp path
  outside its own tree, and the recordings — the only evidence of what the tool actually
  returned — live there. Record `SOCK`/`TMPD`/wall/tokens in the round JSON too: one
  round's recordings were nearly unrecoverable because nobody did.
- **Move `AGENTS.md` out of the working tree while arms run.** It is auto-injected into
  every arm's opening context and it names the fixture and its traps. Restore it
  afterwards.
- **The `writes` fixture's verdict axis has no resolving power.** Four consecutive arms
  reached BOTH_OK with zero re-sends. It catches a careless arm (an a5 arm re-ran the
  non-idempotent step and got caught), and it cannot prove a careful one — so cost and
  confusion are the axes with signal, and `verdict` is a guard rail.

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
