#!/usr/bin/env bash
# E2E-15: screen --find — locate text, click it (1-based click-ready coords).
# The mouse fixture paints "MOUSE_READY q=quit" on visual row 1 and reports
# clicks with 0-based coords (like curses programs do). The fixture is the
# pane's own program, so the row is a property of the fixture, not of a shell
# prompt or of whether a first send was narrated.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
NAME="td15_$$_$RANDOM"
SOCK="td15sock_$$_$RANDOM"
TMP="$(mktemp -d)"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"
export XDG_STATE_HOME="$TMP/state"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-15 FAIL: $*"; exit 1; }

$TD start -n "$NAME" --cmd "python3 $ROOT/tests/fixtures/curses_mouse.py" \
  --socket "$SOCK" --width 120 --height 30 >/dev/null || fail "start failed"
$TD wait -n "$NAME" --until 'MOUSE_READY' --timeout 10 >/dev/null \
  || fail "fixture never started"

# --- find reports 1-based click-ready coordinates ---
$TD screen -n "$NAME" --find quit | python3 -c '
import json, sys
m = json.load(sys.stdin)
assert m == [{"text": "quit", "x": 15, "y": 1}], m
' || fail "--find quit should report x=15 y=1"

# --- the coordinates feed straight into click ---
$TD click -n "$NAME" 15 1 >/dev/null || fail "click failed"
$TD wait -n "$NAME" --until 'CLICKED:14,0' --timeout 5 >/dev/null \
  || fail "click at find-coords should report 0-based CLICKED:14,0"

# --- no match: empty list, not an error ---
$TD screen -n "$NAME" --find ABSENT | python3 -c '
import json, sys
assert json.load(sys.stdin) == []
' || fail "--find ABSENT should return []"

echo "E2E-15 PASS"
