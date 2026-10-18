#!/usr/bin/env bash
# E2E-35: a sentinel must never enter a program's stdin, and an integrated
# shell must never yield the wrong exit code because a prompt marker landed
# after the sentinel's own.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
NAME="td35_$$_$RANDOM"
SOCK="td35sock_$$_$RANDOM"
TMP="$(mktemp -d)"
mkdir -p "$TMP/tmux" "$TMP/home"
export TMUX_TMPDIR="$TMP/tmux"
export XDG_STATE_HOME="$TMP/state"
export HOME="$TMP/home"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-35 FAIL: $*"; exit 1; }

# --- 1. a shell invoked with -c runs a program, not an interactive shell ----
$TD start -n "${NAME}p" --cmd "bash -c 'cat'" --socket "$SOCK" \
  --width 80 --height 20 >"$TMP/prog-start.json" || fail "start (bash -c) failed"
python3 -c "
import json
r = json.load(open('$TMP/prog-start.json'))
assert r.get('marker') == 'unknown', r
" || fail "a program pane must not be advertised as narratable (see above)"

$TD send -n "${NAME}p" --socket "$SOCK" --type "echo TU_INTO_PROGRAM" --key Enter \
  >"$TMP/prog-send.json" || fail "send (program pane) failed"
python3 -c "
import json
r = json.load(open('$TMP/prog-send.json'))
assert r.get('marker') == 'skipped', r
assert r['sent']['text'] == 'echo TU_INTO_PROGRAM', r
assert '__tu_ec' not in r['sent']['text'], f'shell syntax reached a program: {r}'
assert '--until' in (r.get('marker_hint') or ''), r
" || fail "the sentinel was appended to a program's input (see above)"
$TD stop -n "${NAME}p" --socket "$SOCK" >/dev/null

# --- 2. an integrated shell whose first prompt is late ----------------------
# The send happens before bash has painted its first prompt, so the recording
# (correctly) shows no marker yet. The sentinel then runs on an *integrated*
# shell: its D;1 is followed by PROMPT_COMMAND's D;0, because the sentinel's
# printf leaves $? at 0. The wait must read the sentinel's own code.
cat > "$HOME/.bashrc" <<'RC'
while [ ! -e "$HOME/release" ]; do sleep 0.05; done
RC
$TD start -n "$NAME" --cmd bash --socket "$SOCK" \
  --width 80 --height 20 >"$TMP/slow-start.json" || fail "start (slow rc) failed"
python3 -c "
import json
r = json.load(open('$TMP/slow-start.json'))
assert r.get('marker') == 'shell', r
" || fail "an injected bash must report the shell channel (see above)"

$TD send -n "$NAME" --socket "$SOCK" --type "false" --key Enter \
  >"$TMP/slow-send.json" || fail "send (slow rc) failed"
python3 -c "
import json
r = json.load(open('$TMP/slow-send.json'))
assert r.get('marker') == 'sentinel', r
" || fail "the pre-prompt send should have been narrated (see above)"

touch "$HOME/release"
$TD wait -n "$NAME" --socket "$SOCK" --done --timeout 30 >"$TMP/slow-done.json" 2>&1 \
  || { echo "---- $(cat "$TMP/slow-done.json")"; fail "--done did not answer"; }
python3 - "$TMP/slow-done.json" "$XDG_STATE_HOME/term-use/$NAME/raw.log" <<'PY' \
  || fail "the sentinel's code was not the verdict's code (see above)"
import json, sys
v = json.load(open(sys.argv[1]))
assert v['verdict'] == 'met' and v['confidence'] == 'fact', v
assert v.get('exit_code') == 1, f'false reported as {v.get("exit_code")}: {v}'
assert v.get('via') in ('osc133', 'tmux-command-status'), v
note = v.get('note') or ''
assert 'sentinel' in note, note
assert 'emits no OSC 133' not in note, note
raw = open(sys.argv[2], encoding='utf-8', errors='replace').read()
assert '133;D;1' in raw and '133;D;0' in raw, \
    'the fixture did not produce both D markers'
PY

echo "E2E-35 ok"
