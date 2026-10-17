# term-use

term-use is an evidence-driven CLI that gives your coding agents complete control
over **interactive terminal programs** — shells, nano/vim, REPLs, animated TUIs
(ink / bubbletea), even nested tmux — and replaces guessing with proof. Built
agent-first, in the spirit of open-source AI-skill projects like
[superpowers](https://github.com/obra/superpowers) and
[anthropics/skills](https://github.com/anthropics/skills): the docs are written *for
the agent*, the CLI is the tool interface, and every answer comes with evidence
attached.

![version](https://img.shields.io/badge/version-2.0.0a5-orange)
![python](https://img.shields.io/badge/python-3.14%2B-3776AB?logo=python&logoColor=white)
![requires](https://img.shields.io/badge/external%20dep-tmux%203.2a%2b-1BB91F)
![stars](https://img.shields.io/github/stars/uobe1/term-use?style=social)
![issues](https://img.shields.io/github/issues/uobe1/term-use)
![license](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)

## How it works

It starts the moment your agent needs to know something stdout can't tell it: *"did
the command finish?" "what was the exit code?" "which row is selected in that TUI?"*
Instead of blind `sleep`s and screen-scraping prayers, your agent drives the program
inside a real tmux pane and gets verdicts, not vibes:

- Every `wait` returns a verdict with a **confidence level** — `fact` (terminal
  protocol: OSC 133 shell integration, pane death) beats `inference` (screen regex,
  echo-aware) beats `heuristic` (animation-immune quiet sampling).
- Every failure returns **single-line structured JSON** with a screen snapshot
  attached. Timeouts never kill the target — they return evidence.
- A full **protocol-level recording** (`raw.log`, asciicast v2) of every session is
  kept on disk as ground truth: injections, output bytes, sync points.

Because skills trigger by description, your agent doesn't need special instructions —
it reads [the skill](skills/terminal-operation/SKILL.md) and just works.

## Installation

term-use is **two parts**: the `term-use` CLI and the
`terminal-operation` agent **skill**. Install both.

**Automatic (recommended for agents).** Point your agent at the install doc and let it
do the work — paste this into Claude Code / CodeBuddy / Codex / Cursor / any agent
that can read a file or URL. Repo: **https://github.com/uobe1/term-use**

```text
Read docs/en/installation.md in the term-use repo
(https://github.com/uobe1/term-use) and install term-use by following it:
install the `term-use` CLI and the `terminal-operation` skill into
my agent. Tell me what you did.
```

If the agent can only fetch URLs (not local files), use the raw link instead:
`https://raw.githubusercontent.com/uobe1/term-use/main/docs/en/installation.md`.

**Manual.** Open [docs/en/installation.md](docs/en/installation.md) and run the steps
yourself (CLI + skill); the doc is written so an AI agent or a human can follow it
end-to-end.

**Requirements:** Python **3.14+** (stdlib only) and **tmux** on `PATH` — 3.2a minimum,
**3.8+ recommended**: native command status is what keeps completion facts working through
ssh and through prompt frameworks that rewrite `PROMPT_COMMAND`. Pillow optional
(screenshots only); a monospace font ships as a last resort, so `screenshot` works on a
machine with no fonts.

## The agent skill

The repo ships `skills/terminal-operation/` (终端操作) — an agent-facing manual of usage
patterns and hard-won pitfalls. It is not a debugging-only document: it covers any hands-on
terminal work your agent does — driving an interactive program, **running real tests and E2E
scenarios to completion**, reproducing an issue, confirming a command finished with its exit
code, reading or screenshotting a screen, and keeping the context bill down. It is installed
automatically as part of [Installation](#installation) (copied/linked into your agent's skills
directory) and triggers by description, so your agent just works. Read it standalone:
[SKILL.md](skills/terminal-operation/SKILL.md).

## The basic workflow

```bash
term-use start -n demo --cmd bash --width 100 --height 30
term-use send  -n demo --type "make build" --key Enter
term-use wait  -n demo --done --expect-code 0 --timeout 120
# → {"verdict": "met", "confidence": "fact", "via": "osc133", "exit_code": 0, ...}
term-use out   -n demo            # what it printed, inside a context budget
```

That's the whole loop: *start → send → wait for fact → read the output → act*.
`--done` picks the strongest completion proof the target supports (OSC 133 →
the `true` probe → pane death → quiet sampling) and says which it used. The wait
engine also speaks regex (`--until`, plus `--since-send` so a stale anchor can't
satisfy it), quiet detection (`--quiet-ms`), pane death (`--exit`),
AND-composition, and SGR mouse (`click X Y`); `send` handles debounced key
rhythms (`--repeat N --delay S`); `screen --find` turns text into 1-based
click-ready coordinates; `replay --at 3.5` rebuilds the screen as it was at any
earlier moment; `clear` reclaims the pane before the next capture costs you
again; and every observation reports what it cost your context.

**Defaults, decided once.** For waits that is `wait --done`, not `--until`: `--until`
proves some text is on screen (your own echo can supply it) and caps at `inference`,
while `--done` proves the command finished and carries its exit code. Use `--until`
only for a marker you own, and then with `--since-send`. For observation that is the
text channels (`screen --grep/--find/--element-at`, `out`), with `screenshot` once per
layout question — pictures are the expensive exception, not the default, even for
multimodal models. The OSWorld benchmark reports **12.24% for text/a11y observations
against 5.26% for screenshots** (their numbers, not ours), and stacking image history
is what failed there.

**Detailed docs:** [docs/en/usage.md](docs/en/usage.md) — the agent-facing usage guide:
mental model, decision tables, worked examples, common mistakes, red lines.

## Architecture

```
agent → term-use CLI (stateless client) → tmux server → pane (target program)
                                              └→ pipe.py (recorder sink, one per pane)
```

Service/Client split: the CLI is a short-lived, stateless client with zero resident
memory; the tmux server is the service owning PTYs/sessions. All state lives on disk
under `$XDG_STATE_HOME/term-use/<session>/` — `raw.log` (asciicast v2 evidence
store) and `state.json` (atomic locator/recorder state, with `last_send_offset` as a
**byte offset** into `raw.log`, so echo can never fake completion).

Key internal guarantees: the recorder attaches before the target runs (no lost first
bytes); tmux success means exit 0 *and* clean stderr (it can fail and exit 0); text and
keys are never mixed in one `send-keys` call; the OSC 133 A/B/C/D scanner tolerates chunk
boundaries and also reads VSCode's 633; composite verdicts take the weakest member's
confidence; every failure is a fixed-code `TDError` as single-line JSON, exit 1.

## Testing

```bash
# Unit tests — pure asserts, each file runs standalone
python3 tests/unit/test_errors.py
python3 -m pytest tests/unit/

# E2E — standalone bash scripts driving real tmux + real programs (no mocks)
bash tests/e2e/01_start_stop.sh
for t in tests/e2e/*.sh; do bash "$t" || break; done
```

## Contributing

Bug reports, docs and code are welcome — please use **issues + pull requests**
(direct pushes to `main` are blocked). See [CONTRIBUTING.md](CONTRIBUTING.md).

## Star history

[![Star History Chart](https://api.star-history.com/svg?repos=uobe1/term-use&type=Date)
](https://star-history.com/#uobe1/term-use&Date)

## License

Copyright (C) 2026 Uobe

Released under the **GNU General Public License v3.0 or later** (SPDX identifier:
`GPL-3.0-or-later`). See [LICENSE](LICENSE). This program is distributed in the hope
that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty
of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

## Documentation

- Agent-facing user manual:
  [skills/terminal-operation/SKILL.md](skills/terminal-operation/SKILL.md)
- Usage guide: [docs/en/usage.md](docs/en/usage.md)
