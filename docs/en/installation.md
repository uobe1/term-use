# Installing term-use

**term-use** is an evidence-driven CLI that lets coding agents drive and debug
interactive terminal programs — shells, nano/vim, REPLs, ink/bubbletea TUIs, even
nested tmux — through tmux. It replaces blind `sleep`s and screen-scraping with
verdicts backed by evidence.

- Project / source: **https://github.com/uobe1/term-use**
- Agent skill manual: `skills/terminal-operation/SKILL.md`

term-use is **two parts**:

1. the **CLI** (`term-use`) — the tool your agent drives, and
2. the **agent skill** (`terminal-operation`) — the manual that teaches
   your agent *when and how* to drive it.

They install separately, and each has three scopes: **current user**, **one project**,
**whole system**. Install **both** parts; pick the scope that fits per part (the
defaults in [Install steps](#install-steps) are current user for both). There are two
ways to do it — pick whichever fits.

## Requirements

| Component | Version | Why |
|---|---|---|
| Python | **3.14+** | the development and E2E baseline; `requires-python` in `pyproject.toml` enforces it |
| tmux | **3.2a – 3.8+** | the service side. The E2E suite runs on **both 3.7c and 3.8**; 3.8+ is recommended — see the version notes below |
| Pillow | any current release | optional. Only `screenshot` needs it; every other command is Pillow-free |

Check what you have:

```bash
python3 --version
tmux -V
```

No tmux? Install it with the system package manager first (`apt install tmux` on
Debian/Ubuntu, `brew install tmux` on macOS, `pkg install tmux` on Termux/Android,
`dnf install tmux` on Fedora). GNU screen is not a substitute — term-use drives panes
through tmux's own interfaces (`pipe-pane`, `capture-pane -e`, `send-keys`).

**What changes across tmux versions** (measured, not assumed):

| Your tmux | What that means here |
|---|---|
| 3.2a – 3.7c | works on a fallback. `--cmd-done`/`--shell-ready` take their facts from term-use's own recording instead of from tmux, so a stopped recorder or a prompt framework that rewrites `PROMPT_COMMAND` costs the fact channel — and a marker in the recording has to be *attributed* to your send (by a drained recorder, or by the echo of what you typed) before it is called `fact` |
| 3.7 / 3.7a / 3.7b / 3.7c | no difference that matters — the OSC 133 handling and `capture-pane -F` flags are identical across the patch line |
| 3.8 or newer | **recommended, and where the tool is developed.** tmux parses the markers itself, so `wait --done` has a second fact source (`via: tmux-command-status`) that binds a completion to a *start time* rather than to a byte position, survives ssh, a clobbered `PROMPT_COMMAND` and a dead recorder, and `screen --meta` answers "is a command running?" as a fact (`pane_command_running`). Everything else is the same |
| older than 3.2 | unsupported: `capture-pane -e` attribute fidelity and `send-keys -H` byte handling are not reliable there |

term-use has **zero Python dependencies**. tmux is an external binary, not a
pip package.

---

## Option A — automatic install (let your agent do it)

Give your agent this install doc and let it run the steps itself. Works in Claude Code,
CodeBuddy, Codex, Cursor, Gemini CLI, OpenCode — any agent that can read a file or a
URL.

Copy one of these prompts and send it to your agent:

```text
Read docs/en/installation.md in the term-use repo (https://github.com/uobe1/term-use)
and install term-use by following it: the `term-use` CLI (Step 1) and the
`terminal-operation` skill (Step 2), both for the current user unless I say
otherwise. Ask me before choosing a project or system scope. Tell me what you did
and where each part landed.
```

If the agent can only fetch URLs (not local files), use the raw link instead:

```text
Read https://raw.githubusercontent.com/uobe1/term-use/main/docs/en/installation.md and
install term-use by following it: the CLI (Step 1) and the skill (Step 2), both for
the current user unless I say otherwise. Tell me what you did.
```

The agent follows the [Install steps](#install-steps) below — the CLI and the skill
are two artifacts with separate scopes, so it installs both and says where each
landed. No separate script is needed; the doc is the instruction set.

---

## Option B — manual install

Do the [Install steps](#install-steps) yourself, in a terminal. Same result as Option
A: both the CLI and the skill get installed.

<details>
<summary>Why are there two options?</summary>

They install the identical things. Option A hands the same steps to your agent so you
don't type them; Option B is for when you want to do it by hand or the agent isn't
available. Pick one — not both.
</details>

---

## Install steps

Both options run these steps. The agent runs them from Option A; you run them from
Option B.

term-use is **two artifacts that install differently**, and you pick a scope for each:

| Artifact | What it is | Installed by | Scopes |
|---|---|---|---|
| **CLI** | the `term-use` executable — a Python package | `uv tool install`, `pip`, or a venv | user · project · system |
| **Skill** | `skills/terminal-operation/` — documentation, no code, nothing to run | copy or symlink the directory | user · project · system |

They are independent: the CLI works without the skill (read `--help`), and the skill
is dead weight without the CLI. The scopes are independent too — a system-wide CLI
with a per-project skill is a normal setup.

### Step 1 — install the CLI

**For the current user** (default, and what `uv` does by itself):

```bash
uv tool install git+https://github.com/uobe1/term-use.git
# image channel (screenshot) needs Pillow in the tool environment:
uv tool install git+https://github.com/uobe1/term-use.git --with pillow
```

The 2.0 line ships from `main`. New work lands on the alpha branches (`v2.0aN`)
and is merged into `main` once verified, so the commands above install the current
release — do not bake a ref into them. Clone a branch only when you need something
not yet merged:

```bash
git clone -b v2.0a6 https://github.com/uobe1/term-use.git && cd term-use
uv tool install . --with pillow        # or `uv tool install -e .` to hack on it
```

The executable lands in `~/.local/bin`; if `term-use --help` is not found, that
directory is not on `PATH` — run `uv tool update-shell` or export
`PATH="$HOME/.local/bin:$PATH"`.

**For one project** — install it into that project's own environment, so the version
is pinned with the project and nothing global is touched:

```bash
cd /path/to/my-project
uv venv && uv pip install --editable /path/to/term-use          # or the git URL
uv run term-use --help                                          # inside the venv
```

Without `uv`, the same thing is `python3 -m venv .venv && .venv/bin/pip install /path/to/term-use`,
then call `.venv/bin/term-use`. An agent working in that project can also skip the
install entirely and run the source checkout: `PYTHONPATH=/path/to/term-use/src python3 -m termuse …`.

**For every user on the machine** — a venv in a shared read-only location plus one
symlink on `PATH`. This keeps the system Python untouched (no distro-package conflict)
and gives every account the same version:

```bash
sudo python3 -m venv /opt/term-use
sudo /opt/term-use/bin/pip install "term-use[image] @ git+https://github.com/uobe1/term-use.git"
sudo ln -s /opt/term-use/bin/term-use /usr/local/bin/term-use
```

`pip install` on its own (`python3 -m pip install git+…`) also works and goes wherever
the active environment points — which is exactly why the two scopes above spell out
which environment that is.

To try it without installing anything at all:

```bash
uvx --from . term-use --help
```

### Step 2 — install the skill

The skill is the `skills/terminal-operation/` directory in this repo — a core
`SKILL.md` plus the `references/` it points at. Install the whole directory, not
just the core. It is documentation, so nothing is compiled or run: an "install" is
one copy or one symlink, and the only choice is which scope should see it.

**For the current user** — `~/.agents/skills/`, the location most agents and
`npx skills` already agree on, so one install serves all of them:

```bash
SKILLS_DIR="$HOME/.agents/skills"; mkdir -p "$SKILLS_DIR"
ln -s /path/to/term-use/skills/terminal-operation "$SKILLS_DIR/terminal-operation"
```

A symlink stays current with `git pull`; to pin a version, copy instead:

```bash
cp -r /path/to/term-use/skills/terminal-operation "$SKILLS_DIR/terminal-operation"
```

**For one project** — the same two commands pointed at the repository you are
working in. Only agents opened in that project see it, and it travels with the
repo, so a team shares one pinned version:

```bash
cd /path/to/my-project
mkdir -p .agents/skills
ln -s /path/to/term-use/skills/terminal-operation .agents/skills/terminal-operation
```

Add `.agents/` to `.gitignore` if you symlink (the path is machine-local); if you
copy it, commit it and the project carries its own skill.

**For every user on the machine** — one read-only copy in a shared place, then a
symlink per account pointing at it. There is no cross-agent standard for a
system-wide skills directory, so the shared copy plus per-user links is the form
that works everywhere; if your own agent does document a system directory, put the
link there instead and skip the per-user step:

```bash
sudo mkdir -p /usr/local/share/agents/skills
sudo cp -r /path/to/term-use/skills/terminal-operation /usr/local/share/agents/skills/
# then, as each user:
mkdir -p "$HOME/.agents/skills"
ln -s /usr/local/share/agents/skills/terminal-operation "$HOME/.agents/skills/terminal-operation"
```

If your agent reads only its own directory, everything above is the same command
with a different path:

| Agent | Skills directory |
|---|---|
| Claude Code | `~/.claude/skills/` |
| Codex | `~/.codex/skills/` |
| Cursor | `~/.cursor/skills/` |
| Gemini CLI | `~/.gemini/skills/` |
| OpenCode | `~/.config/opencode/skills/` |

Restart the agent (or start a fresh session) so it picks up the new skill. It then
triggers automatically by description — no further setup.

### Step 3 — verify

```bash
term-use sessions
# → [] — or a JSON array of existing tmux sessions; both are success

ls "$SKILLS_DIR/terminal-operation/SKILL.md"   # skill present
```

A real end-to-end check that the CLI *and* the method work:

```bash
term-use start -n install-check --cmd bash --width 80 --height 24
term-use send  -n install-check --type "echo ok" --key Enter
term-use wait  -n install-check --cmd-done --expect-code 0 --timeout 10
# → {"verdict": "met", "confidence": "fact", "exit_code": 0, ...}
term-use stop  -n install-check
```

All five commands must complete without an `{"error": ...}` object on stderr. The
`confidence: fact` in the wait verdict is the part that matters — it means the exit
code came from the shell protocol (OSC 133), not from screen guessing.

---

### Migrating from the old shim layout

Early v2 checkouts exposed a root-level `term_debug.py` shim. It has been removed;
the package now lives in `src/termuse/` and runs as `python -m termuse`. If your
scripts still call `python3 term_debug.py`, replace the call with `term-use`
(installed) or `PYTHONPATH=<repo>/src python3 -m termuse` (source tree).

Session data has moved too — twice, and it changed name along the way. Nothing is
read from or migrated out of the old locations, so a session started before the
rename looks like an empty directory:

| when the session was started | where its recordings are |
|---|---|
| cache era (before the `src/` layout refactor) | `$XDG_CACHE_HOME/term-debug/` → `~/.cache/term-debug/` |
| 2.0.0a1 – 2.0.0a2 | `$XDG_STATE_HOME/term-debug/` → `~/.local/state/term-debug/` |
| 2.0.0a3 and later | `$XDG_STATE_HOME/term-use/` → `~/.local/state/term-use/` |

```bash
mkdir -p ~/.local/state/term-use
cp -a ~/.local/state/term-debug/. ~/.local/state/term-use/   # a1–a2 recordings
cp -a ~/.cache/term-debug/. ~/.local/state/term-use/         # cache-era recordings
```

That restores the evidence (`raw.log`, `state.json`, replayable frames). It does not
hand back a live pane: the tmux session those recordings described is gone, so
`wait`/`send` against a copied name will not resume. Per the XDG Base Directory
spec, recordings and locator state are *logs / current state*, not discardable
cache — which is why they left `~/.cache` in the first place.

## Updating

```bash
uv tool upgrade term-use
# or
python3 -m pip install -U git+https://github.com/uobe1/term-use.git
```

To pin a version while the 2.0.0 alpha line is moving, append a git ref to the
URL — `@<tag>` or `@<commit>`:

```bash
uv tool install git+https://github.com/uobe1/term-use.git@<commit>
```

(No tags have been cut yet; pin a commit if you need stability.)

If you installed the skill by symlink from a clone, re-run `git pull` in the clone to
refresh it. If you copied it, re-copy from a fresh clone — and if you installed the
system-wide copy, refreshing `/usr/local/share/agents/skills/terminal-operation`
updates every account whose symlink points at it.

## Uninstall

Remove the two artifacts separately, from whichever scope you used.

CLI:

```bash
uv tool uninstall term-use                    # current user
.venv/bin/pip uninstall term-use              # one project (run from inside it)
sudo rm -f /usr/local/bin/term-use && sudo rm -rf /opt/term-use   # system
# or, if you installed with pip into an active environment:
python3 -m pip uninstall term-use
```

Skill:

```bash
rm -rf "$SKILLS_DIR/terminal-operation"       # user or project scope
sudo rm -rf /usr/local/share/agents/skills/terminal-operation   # system
```

A symlinked skill is one link: removing it leaves the clone (and the shared system
copy) untouched.

Recordings under `$XDG_STATE_HOME/term-use/` are plain files; delete the
directory for a full purge.

## Troubleshooting

### `term-use: command not found` after uv tool install

uv's tool bin is not on `PATH`. Run `uv tool update-shell` (adds it and applies on
next login) or export `PATH="$HOME/.local/bin:$PATH"` in the current shell.

### `{"error":{"code":"pillow-missing",...}}` from `screenshot`

Expected without Pillow — the error is structured, not a crash. Reinstall with
`--with pillow`, or set `TERM_USE_DISABLE_IMAGE=1` to make the text-only behavior
explicit.

### `{"error":{"code":"no-shell-integration",...}}` from `--cmd-done`

The pane's shell emits no OSC 133 command markers. Integration is injected for
**both bash and zsh** (`start` builds a `ZDOTDIR` of its own that sources your files
first), **fish >= 4.0** emits the markers by itself, and a session carrying someone
else's integration (VSCode's OSC 633, WezTerm's 133) is believed from the stream
rather than refused for not being a shell we configured. So this error means one of:

- the pane runs a shell with no pre-exec hook — plain `sh`/`dash`/`csh`. Wait on the
  program's own output with `--until 'regex' --since-send`, or on pane death with
  `--exit` when the program *is* the pane;
- a prompt framework (starship, powerline) rewrote the injected hooks after startup.
  `--until` still works, and on tmux 3.8+ term-use also asks tmux's own command
  status, which keeps answering when `PROMPT_COMMAND` is gone;
- the shell had not painted its first prompt while the gate looked. It waits up to
  `min(--timeout, 5s)` for the stream to prove itself, so a longer `--timeout` is
  the fix, not a different shell.

`--cmd zsh` and `--cmd fish` are supported routes to a fact-level wait; only
`sh`/`dash` have no completion marker to wait for — and even there `send` appends a
completion sentinel of its own to the line it submits, so `wait --done` still answers
at `fact` with the exit code. The send result states which happened
(`marker: shell | sentinel | skipped`) and a met verdict says in its `note` when the
marker was synthesized rather than confessed by the shell. Set `TERM_USE_SENTINEL=0`
to forbid that append: the refusal comes back, honestly.

### `{"error":{"code":"shell-not-ready",...}}` from `--shell-ready`

The `true` probe got no answer in time — usually a live interactive program
swallowed it (that is the signal working as designed: send the program's quit
key and re-probe). If the shell was genuinely still starting, retry with a
longer `--timeout`.

### `{"error":{"code":"unmanaged-pane",...}}` from `--cmd-done`

The pane exists in tmux but was not started by term-use (e.g. a nested tmux
pane) — there is no shell-protocol channel on it. The screen channels
(`--until`, `--quiet-ms`) still work there with degraded (heuristic) confidence;
start a managed session (`term-use start -n <name> --cmd bash`) when you need
facts. If the pane IS a term-use session, your `XDG_STATE_HOME` differs
between `start` and the later command — keep it stable.

### Skill not triggering / agent ignores term-use

The skill symlink/copy may be in the wrong directory, or the agent session started
before the skill was added. Confirm
`$SKILLS_DIR/terminal-operation/SKILL.md` exists (Step 2 sets that path; use your
agent's own skills directory if it does not read `~/.agents/skills`), then restart
the agent or open a fresh session.

### Sessions "disappear" between commands

`XDG_STATE_HOME` changed between `start` and the later command. Locator and recorder
state live under `$XDG_STATE_HOME/term-use/<session>/`; keep that variable stable
for the whole working session.

## Getting help

- Issues: https://github.com/uobe1/term-use/issues
- Driving patterns for agents: [usage.md](usage.md)
- Agent skill manual: [SKILL.md](../../skills/terminal-operation/SKILL.md)

When filing a bug, paste the structured error JSON verbatim — it already contains the
screen snapshot, cursor position and raw-stream tail needed to reproduce.
