#!/usr/bin/env bash
# E2E-25: fact-level completion outside bash.
#   zsh  — hooks injected through ZDOTDIR, without clobbering the user's setup
#   fish — emits OSC 133 natively (>=4.0), so term-use only has to *notice*
# The point: `--cmd-done`/`--shell-ready` are the strongest evidence the tool
# offers, and refusing them on every non-bash shell was a portability hole
# (zsh is the macOS default).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td25sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-25 FAIL: $*"; exit 1; }

probe_shell() {   # $1 = shell name
  local sh="$1" name="td25_$1"
  command -v "$sh" >/dev/null || { echo "  (skip: $sh not installed)"; return 0; }
  $TD start -n "$name" --cmd "$sh" --socket "$SOCK" --width 80 --height 20 \
    >/dev/null || fail "$sh: start failed"
  # The probe: fact-level "the shell is reading my input" — for fish this only
  # works if term-use recognised the markers the shell already emits.
  if $TD wait -n "$name" --shell-ready --timeout 15 | python3 -c '
import json, sys
v = json.load(sys.stdin)
assert v["confidence"] == "fact", f"{v}"
'; then :; else fail "$sh: --shell-ready did not reach fact confidence"; fi
  # exit codes, from the shell protocol
  $TD send -n "$name" --type "false" --key Enter >/dev/null
  $TD wait -n "$name" --cmd-done --expect-code 1 --timeout 15 >/dev/null \
    || fail "$sh: --cmd-done must read exit code 1"
  $TD send -n "$name" --type "printf 'Z25_OK\n'" --key Enter >/dev/null
  $TD wait -n "$name" --cmd-done --expect-code 0 --timeout 15 >/dev/null \
    || fail "$sh: --cmd-done must read exit code 0"
  $TD wait -n "$name" --until 'Z25_OK' --timeout 5 >/dev/null \
    || fail "$sh: output never appeared"
  $TD stop -n "$name" >/dev/null
  echo "  $sh: fact-level completion OK"
}

echo "E2E-25 shells:"
probe_shell zsh
probe_shell fish

# --- zsh injection must not swallow the user's own configuration -----------
if command -v zsh >/dev/null; then
  mkdir -p "$TMP/zhome"
  # Written before the session starts: zsh sources .zshrc once, so appending
  # afterwards would test nothing.
  printf 'export TD_ZSH_MARK=seen\nprecmd() { echo PRECMD_RAN; }\n' > "$TMP/zhome/.zshrc"
  HOME="$TMP/zhome" $TD start -n td25zr --cmd zsh --socket "$SOCK" \
    --width 80 --height 20 >/dev/null || fail "zsh start (user rc) failed"
  $TD wait -n td25zr --shell-ready --timeout 15 >/dev/null || fail "zsh not ready"
  $TD send -n td25zr --type 'echo MARK=$TD_ZSH_MARK' --key Enter >/dev/null
  $TD wait -n td25zr --until 'MARK=seen' --timeout 10 >/dev/null \
    || fail "the user's ~/.zshrc must still run under our ZDOTDIR"
  # a user-defined raw precmd() must survive too (hook arrays compose)
  $TD wait -n td25zr --until 'PRECMD_RAN' --timeout 10 >/dev/null \
    || fail "a user precmd() must still run alongside our hooks"
  $TD stop -n td25zr >/dev/null
fi

# --- dash has no pre-exec hook: refuse honestly, never fake a fact ----------
if command -v dash >/dev/null; then
  $TD start -n td25d --cmd dash --socket "$SOCK" --width 60 --height 12 >/dev/null
  if $TD wait -n td25d --cmd-done --timeout 3 >/dev/null 2>"$TMP/dash.json"; then
    fail "dash must not claim shell integration it cannot have"
  fi
  python3 -c "
import json
e = json.load(open('$TMP/dash.json'))['error']
assert e['code'] == 'no-shell-integration', e
assert 'zsh' in (e['hint'] or '') or 'bash' in (e['hint'] or ''), e
" || fail "dash must get the structured refusal (see above)"
  $TD stop -n td25d >/dev/null
fi

echo "E2E-25 PASS"
