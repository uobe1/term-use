#!/usr/bin/env bash
# E2E-27: tmux's own command status, when the server keeps one.
#
#   3.2a–3.7c: the pane_command_* formats do not exist and tmux expands every
#              unknown format to *empty* — so meta must DROP the group. An
#              empty value would read as "no command has finished yet", which
#              is a different claim from "this server cannot answer".
#   3.8+     : tmux parses OSC 133 itself and keeps the exit code. That is a
#              second fact channel, and the one that still works when our own
#              recorder does not: with pipe-pane detached, raw.log receives no
#              output at all, so term-use's byte scan can never see another
#              completion marker while tmux goes on counting them.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td27sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-27 FAIL: $*"; exit 1; }

VER="$(tmux -V | awk '{print $2}')"
NATIVE=0
case "$VER" in 3.8 | 3.9 | next-*) NATIVE=1 ;; esac
echo "tmux $VER -> native command status expected: $NATIVE"

$TD start -n td27 --cmd bash --socket "$SOCK" --width 90 --height 25 >/dev/null \
  || fail "start failed"
PANE="$(tmux -L "$SOCK" display-message -p -t td27 '#{pane_id}')"

# --- 1. the meta surface is version-shaped, never shaped like an answer -----
$TD send -n td27 --type "echo TD27_META" --key Enter >/dev/null
$TD wait -n td27 --socket "$SOCK" --cmd-done --timeout 15 >/dev/null \
  || fail "setup command never finished"
$TD screen -n td27 --socket "$SOCK" --meta >"$TMP/meta.json" || fail "screen --meta"
python3 -c "
import json
m = json.load(open('$TMP/meta.json'))
native = 'pane_command_running' in m
assert native == bool($NATIVE), f'the group must exist exactly where it does: {m}'
if native:
    # 0/1 always — a None here would mean the key exists but was never answered
    assert m['pane_command_running'] in (0, 1), m
    assert isinstance(m['pane_command_status'], int), m
    assert isinstance(m['pane_output_generation'], int), m
else:
    for k in ('pane_command_status', 'pane_command_running',
              'pane_command_end_time', 'pane_output_generation'):
        assert k not in m, f'{k} must be absent on <=3.7c, not empty: {m}'
" || fail "meta version shape (see above)"

if [ "$NATIVE" = 1 ]; then
  # --- 2. with our recorder detached, only tmux's channel can answer --------
  tmux -L "$SOCK" pipe-pane -t "$PANE" >/dev/null 2>&1 || fail "detach the recorder"
  sleep 0.2
  [ "$(tmux -L "$SOCK" display-message -p -t "$PANE" '#{pane_pipe}')" = "0" ] \
    || fail "tmux still pipes this pane"
  $TD send -n td27 --socket "$SOCK" --type "echo TD27_DETACHED" --key Enter >/dev/null
  $TD wait -n td27 --socket "$SOCK" --cmd-done --timeout 15 >"$TMP/detached.json" 2>&1 \
    || fail "the native channel must answer while the recorder is blind"
  python3 -c "
import json
v = json.load(open('$TMP/detached.json'))
assert v['confidence'] == 'fact', v
assert v.get('via') == 'tmux-command-status', \
    f'which channel proved this must be named: {v}'
assert v['conditions'][0]['exit_code'] == 0, v
" || fail "detached-recorder wait (see above)"
  # and prove the scan really was blind: the bytes never reached the recording
  if $TD out -n td27 --socket "$SOCK" 2>/dev/null | grep -q TD27_DETACHED; then
    fail "raw.log still received pane output, so the recorder was never blind"
  fi

  # --- 3. a C with no D: show tmux's reading, do not pick a story -----------
  # `read` is a builtin, so the foreground stays bash while no completion
  # marker arrives and the pane goes quiet. A prompt framework that swallowed
  # PROMPT_COMMAND leaves *exactly* the same mark, so the answer has to carry
  # tmux's own reading (running=true) and name both possibilities — never push
  # a quit key at a shell that may merely be idle.
  $TD send -n td27 --socket "$SOCK" --type 'TD27_V=; read TD27_V' --key Enter >/dev/null
  sleep 2
  if $TD wait -n td27 --socket "$SOCK" --done --timeout 2 >/dev/null 2>&1; then
    fail "--done must not meet while the shell is inside read"
  fi
  $TD wait -n td27 --socket "$SOCK" --done --timeout 2 2>"$TMP/inflight.json" || true
  python3 -c "
import json
e = json.load(open('$TMP/inflight.json'))['error']
assert e['code'] in ('shell-protocol-unavailable', 'timeout'), e['code']
nat = (e.get('evidence') or {}).get('native')
assert nat and nat['running'] is True, \
    'tmux says a command is in flight, so the evidence must say so too: ' + str(e)
h = e.get('hint') or ''
assert 'waiting for input' in h and 'PROMPT_COMMAND' in h, \
    'the hint must name both readings of a silent C: ' + h
assert 'quit key' in h, 'the hint must still forbid the blind quit key: ' + h
" || fail "in-flight evidence (see above)"
else
  # on <=3.7c the same waits have to be answered by our own scan, and only by it
  $TD send -n td27 --socket "$SOCK" --type "echo TD27_SCAN" --key Enter >/dev/null
  $TD wait -n td27 --socket "$SOCK" --done --timeout 15 | python3 -c "
import json, sys
v = json.load(sys.stdin)
assert v['confidence'] == 'fact' and v.get('via') == 'osc133', v
" || fail "the byte scan must still reach fact on <=3.7c (see above)"
fi

$TD stop -n td27 --socket "$SOCK" >/dev/null
echo "E2E-27 PASS"
