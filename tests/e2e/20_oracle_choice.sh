#!/usr/bin/env bash
# E2E-20: help the agent pick the right wait.
#   `wait --done`  — finish-the-last-thing-I-sent, using the STRONGEST oracle
#                    this target actually supports, and saying which one it used.
#   `--since-send` — a --until that can only be satisfied by output produced
#                    after the last send, which kills the stale-anchor class of
#                    false positives the skill warns about.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td20sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-20 FAIL: $*"; exit 1; }

$TD start -n td20 --cmd bash --socket "$SOCK" --width 90 --height 25 >/dev/null \
  || fail "start failed"

# --- 1. --done right after start: no footgun timeout. Nothing has been sent,
#        so the strongest available proof is the `true` probe. ---
$TD wait -n td20 --done --timeout 10 | python3 -c '
import json, sys
v = json.load(sys.stdin)
assert v["verdict"] == "met" and v["confidence"] == "fact", v
assert v["via"] == "shell-ready-probe", f"--done must say which oracle answered: {v}"
' || fail "--done after start (see above)"

# --- 2. --done after a real command: fact via OSC 133, with the exit code ---
$TD send -n td20 --type "sleep 1; echo DONE20_A" --key Enter >/dev/null
$TD wait -n td20 --done --expect-code 0 --timeout 20 | python3 -c '
import json, sys
v = json.load(sys.stdin)
assert v["confidence"] == "fact", v
assert v["via"] == "osc133", v
assert v["exit_code"] == 0, v
assert "DONE20_A" in v["evidence"]["screen"], "verdict should still show the screen"
' || fail "--done after a command (see above)"

# a failing command reports the code, and --expect-code mismatches fast
$TD send -n td20 --type "false" --key Enter >/dev/null
$TD wait -n td20 --done --timeout 10 | python3 -c '
import json, sys
v = json.load(sys.stdin)
assert v["exit_code"] == 1, v
' || fail "--done must read the exit code"

# --- 3. advice: tell the agent when a stronger oracle exists ---------------
# Anchoring on text we typed is the echo case: the verdict must admit it
# (echo_suspect) *and* point at the fact oracle this session can use.
$TD send -n td20 --type "echo PLAIN_ANCHOR_20" --key Enter >/dev/null
$TD wait -n td20 --until 'PLAIN_ANCHOR_20' --timeout 10 | python3 -c '
import json, sys
v = json.load(sys.stdin)
c = v["conditions"][0]
assert c["echo_suspect"] is True, f"anchored on our own typed text: {c}"
assert v["confidence"] == "heuristic", "echo-suspect must downgrade: " + v["confidence"]
adv = v.get("advice") or ""
assert "--done" in adv and "fact" in adv, f"no pointer to the stronger oracle: {adv}"
' || fail "--until should advertise the stronger channel (see above)"

# --- 4. --since-send cannot be fooled by a stale anchor ---
$TD send -n td20 --type "echo STALE_MARK" --key Enter >/dev/null
$TD wait -n td20 --cmd-done --timeout 10 >/dev/null || fail "first cmd never finished"
# STALE_MARK is still sitting on the screen. A plain --until meets instantly on
# the old text; scoped to output since this send, it must not.
$TD send -n td20 --type "echo SOMETHING_ELSE" --key Enter >/dev/null
if $TD wait -n td20 --until 'STALE_MARK' --timeout 3 >/dev/null 2>&1; then
  : # expected: the stale anchor matches the current screen (documented behavior)
else
  fail "plain --until should still match the stale screen (baseline for the next assert)"
fi
if $TD wait -n td20 --until 'STALE_MARK' --since-send --timeout 3 >/dev/null 2>&1; then
  fail "--since-send matched text from BEFORE the last send — stale anchor survived"
fi
$TD wait -n td20 --until 'SOMETHING_ELSE' --since-send --timeout 10 | python3 -c '
import json, sys
v = json.load(sys.stdin)
assert v["verdict"] == "met", v
c = v["conditions"][0]
assert c["type"] == "until" and c.get("scope") == "since-send", v
' || fail "--since-send must meet on genuinely new output (see above)"

# --- 5. an unmanaged pane has no offset to scope by: refuse structurally ---
tmux -L "$SOCK" new-session -d -s stray20 'sleep 60' >/dev/null 2>&1 || fail "stray failed"
sleep 0.5
if $TD wait -n stray20 --socket "$SOCK" --until 'x' --since-send --timeout 2 \
     >/dev/null 2>"$TMP/um.json"; then
  fail "--since-send on an unmanaged pane must refuse"
fi
python3 -c "
import json
e = json.load(open('$TMP/um.json'))['error']
assert e['code'] == 'unmanaged-pane', e
" || fail "expected unmanaged-pane for --since-send (see above)"

$TD stop -n td20 >/dev/null
tmux -L "$SOCK" kill-session -t stray20 >/dev/null 2>&1 || true
echo "E2E-20 PASS"
