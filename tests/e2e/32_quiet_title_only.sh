#!/usr/bin/env bash
# E2E-32: quiet detection answers the screen, not the byte count.
#
# A program can hold its output stream open without painting a single cell —
# an ink TUI that rewrites the *window title* with spinner frames every 100 ms
# keeps the screen byte-identical while raw.log fills with `ESC ]0;… BEL`.
# Gate 1 used to count those bytes as activity, so `wait --quiet-ms 3000
# --timeout 240` burned the whole timeout on a still screen (issue #5).
#
# Both halves are pinned, because over-stripping is the same bug wearing the
# opposite face: a program that paints spinner *cells* must still never settle.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
NAME="td32_$$_$RANDOM"
SOCK="td32sock_$$_$RANDOM"
TMP="$(mktemp -d)"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"
export XDG_STATE_HOME="$TMP/state"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-32 FAIL: $*"; exit 1; }

$TD start -n "$NAME" --cmd bash --socket "$SOCK" --width 90 --height 24 >/dev/null \
  || fail "start failed"
$TD send -n "$NAME" --type "true" --key Enter >/dev/null || fail "warm-up send"
$TD wait -n "$NAME" --cmd-done --timeout 15 >/dev/null || fail "warm-up never finished"

# --- 1. title-only animation settles, and says the program is still running --
$TD send -n "$NAME" \
  --type "for i in \$(seq 40); do printf '\\033]0;s\$i\\007'; sleep .1; done" \
  --key Enter >/dev/null || fail "send (title spinner)"
set +e
QUIET="$($TD wait -n "$NAME" --quiet-ms 500 --timeout 6 2>&1)"
RC=$?
set -e
[ "$RC" = 0 ] || { echo "---- $QUIET"; fail "a title-only stream never settled"; }
printf '%s\n' "$QUIET" | python3 -c "
import json, sys
v = json.loads(sys.stdin.read().strip().splitlines()[-1])
assert v['verdict'] == 'met' and v['confidence'] == 'heuristic', v
# Settling a still screen while the loop runs is only honest if the verdict says
# the program is not finished — that is the difference between 'idle' and 'done'.
q = json.dumps(v['conditions'])
assert '\"program_running\": true' in q or 'silent_unproven' in q, v
assert 'not the same as' in (v.get('note') or ''), v.get('note')
" || fail "the title-spinner settle was not reported honestly (see above)"

# The screen really did stay empty of frames: the title never reached the grid.
$TD wait -n "$NAME" --cmd-done --timeout 20 >/dev/null || fail "spinner never finished"

# --- 2. cell-painting animation must NOT settle ------------------------------
$TD send -n "$NAME" \
  --type "for i in \$(seq 40); do printf 'x%s ' \$i; sleep .1; done" \
  --key Enter >/dev/null || fail "send (cell spinner)"
set +e
PAINT="$($TD wait -n "$NAME" --quiet-ms 500 --timeout 3 2>&1)"
RC=$?
set -e
if [ "$RC" = 0 ]; then
  echo "---- $PAINT"
  fail "a program painting new cells every 100 ms settled as if it were idle"
fi
printf '%s\n' "$PAINT" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read().strip().splitlines()[-1])
assert d['error']['code'] == 'timeout', d
" || fail "the painting case failed for the wrong reason (see above)"

# --- 3. an OSC 133 marker storm is not activity either ----------------------
# The markers term-use itself waits for paint nothing; a shell hooked up to emit
# them on a timer used to hold --quiet-ms open forever.
$TD send -n "$NAME" \
  --type "for i in \$(seq 30); do printf '\\033]133;A\\007'; sleep .1; done" \
  --key Enter >/dev/null || fail "send (marker storm)"
set +e
MARK="$($TD wait -n "$NAME" --quiet-ms 500 --timeout 6 2>&1)"
RC=$?
set -e
[ "$RC" = 0 ] || { echo "---- $MARK"; fail "a marker-only stream never settled"; }

echo "E2E-32 ok"
