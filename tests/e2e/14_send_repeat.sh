#!/usr/bin/env bash
# E2E-14: send --repeat N --delay S — debounced menus / double-key rhythms.
# The menu_debounce fixture applies an arrow only if >= 0.45s elapsed since
# the last applied arrow and drops the rest (debounced menu widget behavior).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
NAME="td14_$$_$RANDOM"
SOCK="td14sock_$$_$RANDOM"
TMP="$(mktemp -d)"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"
export XDG_STATE_HOME="$TMP/state"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-14 FAIL: $*"; exit 1; }

selected() { $TD screen -n "$NAME" | grep -o 'SELECTED:[0-9]*'; }

# The debounce window must be wider than the gap *inside* one term-use `send`
# (two send-keys subprocesses, 0.1-1.5s apart on a loaded box) and narrower than
# the --delay between repetitions. At the fixture's 0.45s default this control
# failed under load and the failure was the machine's, not the tool's.
export MENU_DEBOUNCE_S=1.5
$TD start -n "$NAME" --cmd bash --socket "$SOCK" --width 100 --height 30 >/dev/null \
  || fail "start failed"
$TD send -n "$NAME" --type "python3 $ROOT/tests/fixtures/menu_debounce.py" --key Enter \
  >"$TMP/first.json" || fail "send fixture failed"
$TD wait -n "$NAME" --until 'SELECTED:0' --timeout 10 >/dev/null || fail "fixture never started"

# --- control: one call with two Ups is debounced to a single move ---
$TD send -n "$NAME" --key Up --key Up >/dev/null || fail "send failed"
# The gap must be wider than the debounce window: the first repetition is a
# separate process, and on a fast box it can arrive within the window measured
# from the control's applied Up. Sleep past it before starting the repeat.
sleep 2
[ "$(selected)" = "SELECTED:1" ] || fail "one-call two Ups should move once, got: $(selected)"

# --- --repeat 2 --delay 2.2 (> the debounce window): each repetition is a full
# --- independent send, so the menu applies both
BEFORE="$(grep -o '"key": "Up"' "$XDG_STATE_HOME/term-use/$NAME/raw.log" | wc -l)"
$TD send -n "$NAME" --key Up --repeat 2 --delay 2.2 >"$TMP/repeat.json" \
  || fail "send --repeat failed"
sleep 2
[ "$(selected)" = "SELECTED:3" ] || fail "--repeat 2 --delay 2.2 should move twice, got: $(selected)"
AFTER="$(grep -o '"key": "Up"' "$XDG_STATE_HOME/term-use/$NAME/raw.log" | wc -l)"
[ "$((AFTER - BEFORE))" -eq 2 ] || fail "expected 2 i-event Up records, got $((AFTER - BEFORE))"

# --- the reply must be readable, not a re-print of the event list ---
# `sent` used to carry the raw i-events: the typed text as JSON objects, twice
# over under --repeat 2. The caller cannot check "what went in" from that, and
# it is bytes it already paid.
python3 -c "
import json
first = json.load(open('$TMP/first.json'))
s = first['sent']
head = s['text'].split('; __tu_ec')[0]
assert head.endswith('menu_debounce.py') and s['keys'] == ['Enter'], first
# a lengthened line is only acceptable when the reply says so (see e2e-33)
assert ('; __tu_ec' not in s['text']) or first.get('marker') == 'sentinel', first
assert s['calls'] == 2, first
assert 'repeat' not in first, f'--repeat 1 must not print a constant: {first}'
rep = json.load(open('$TMP/repeat.json'))
assert 'text' not in rep['sent'] and rep['sent']['keys'] == ['Up', 'Up'], rep
assert rep['sent']['calls'] == 2 and rep['repeat'] == 2, rep
" || fail "send's reply shape (see above)"

# --- red line: each repetition must stay text/key separated (works at all) ---
$TD send -n "$NAME" --key Down --repeat 1 >/dev/null || fail "repeat 1 failed"
sleep 1
[ "$(selected)" = "SELECTED:2" ] || fail "repeat 1 Down should move once, got: $(selected)"

# --- validation: repeat < 1 is a structured error, nothing is sent ---
if $TD send -n "$NAME" --key Up --repeat 0 >/dev/null 2>"$TMP/bad.json"; then
  fail "--repeat 0 must fail"
fi
python3 -c "
import json,sys
e=json.load(open('$TMP/bad.json'))['error']
assert e['code']=='invalid-arguments', e
" || fail "expected invalid-arguments error for --repeat 0"

# --- validation ordering: the same bad args must fail identically on a
#     target that doesn't exist (error code independent of session state) ---
if $TD send -n "td14_absent_$$_$RANDOM" --key Up --repeat 0 \
     >/dev/null 2>"$TMP/absent.json"; then
  fail "--repeat 0 on a missing target must fail"
fi
python3 -c "
import json,sys
e=json.load(open('$TMP/absent.json'))['error']
assert e['code']=='invalid-arguments', e
" || fail "expected invalid-arguments regardless of target existence"

# --- bad hex is an argument error, not a runtime/target error ---
if $TD send -n "$NAME" --hex zz >/dev/null 2>"$TMP/hex.json"; then
  fail "--hex zz must fail"
fi
python3 -c "
import json,sys
e=json.load(open('$TMP/hex.json'))['error']
assert e['code']=='invalid-arguments', e
" || fail "expected invalid-arguments error for --hex zz"

echo "E2E-14 PASS"
