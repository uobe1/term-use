#!/usr/bin/env bash
# E2E-30: three things both arms of the ab_write A/B reported being misled by.
#   1. `--quiet-ms` returning met while a silent program still holds the pane —
#      a statement about pixels read as a statement about completion.
#   2. `out` claiming the program repositions text (and sending the agent to
#      `replay`) for an ordinary colored Python traceback.
#   3. `sessions --socket NAME` failing with socket-unreachable before the first
#      `start`, when the honest answer is "that room is empty".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td30sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-30 FAIL: $*"; exit 1; }

# --- 3. an untouched private socket is an empty list, not a fault -----------
if ! out=$($TD sessions --socket "$SOCK" 2>"$TMP/s.err"); then
  fail "sessions on a socket with no server yet must succeed, got: $(cat "$TMP/s.err")"
fi
[ "$out" = "[]" ] || fail "expected an empty list, got: $out"
grep -qi 'no tmux server .* yet' "$TMP/s.err" \
  || fail "the empty answer must still be explained on stderr: $(cat "$TMP/s.err")"

$TD start -n td30 --cmd bash --socket "$SOCK" --width 90 --height 24 >/dev/null \
  || fail "start failed"
$TD wait -n td30 --socket "$SOCK" --shell-ready --timeout 15 >/dev/null \
  || fail "shell not ready"

# --- 1. quiet settles, but must not be misread as completion ---------------
$TD send -n td30 --socket "$SOCK" --type "python3 -c 'import time; time.sleep(6)'" \
  --key Enter >/dev/null || fail "send failed"
sleep 1.5                       # the echo has landed; the program is now silent
$TD wait -n td30 --socket "$SOCK" --quiet-ms 700 --timeout 5 \
  >"$TMP/quiet.json" 2>&1 || fail "quiet should settle on a still screen"
python3 -c "
import json
v = json.load(open('$TMP/quiet.json'))
c = v['conditions'][0]
assert v['verdict'] == 'met', v
assert c.get('program_running') is True, \
    f'a program holds the pane; the verdict must say so: {c}'
n = v.get('note') or ''
assert 'repainting' in n and '--done' in n, f'the note must name the boundary: {n!r}'
" || fail "quiet honesty (see above)"

# and the completion question is still answerable, at fact level
$TD wait -n td30 --socket "$SOCK" --done --expect-code 0 --timeout 20 \
  | python3 -c '
import json, sys
v = json.load(sys.stdin)
assert v["confidence"] == "fact" and v["exit_code"] == 0, v
assert "note" not in v, f"--done has nothing to caveat: {v}"
' || fail "--done must still prove completion after the silent stretch"

# --- 2. a colored traceback is not a redrawn program -----------------------
$TD send -n td30 --socket "$SOCK" --type "python3 -c 'raise ValueError(1)'" \
  --key Enter >/dev/null
$TD wait -n td30 --socket "$SOCK" --done --timeout 15 >/dev/null || fail "traceback never ran"
$TD out -n td30 --socket "$SOCK" --all >"$TMP/out.txt" 2>"$TMP/out.err" \
  || fail "out failed"
grep -q 'ValueError' "$TMP/out.txt" || fail "traceback missing from out"
if grep -qi 'repositions text with cursor escapes' "$TMP/out.err"; then
  fail "out called a plain traceback a redraw: $(cat "$TMP/out.err")"
fi

# a real redraw still gets the warning (the note is not merely switched off)
cat > "$TMP/paint.py" <<'PY'
import sys, time
sys.stdout.write("\x1b[?1049h\x1b[2Jframe one\n")
sys.stdout.flush(); time.sleep(0.4)
sys.stdout.write("\x1b[2Jframe two\n"); sys.stdout.flush()
sys.stdout.write("\x1b[?1049l")
PY
$TD send -n td30 --socket "$SOCK" --type "python3 $TMP/paint.py" --key Enter >/dev/null
$TD wait -n td30 --socket "$SOCK" --done --timeout 15 >/dev/null || fail "paint never ran"
$TD out -n td30 --socket "$SOCK" --all >/dev/null 2>"$TMP/paint.err" \
  || fail "out on the painting program failed"
grep -qi 'repositions text with cursor escapes' "$TMP/paint.err" \
  || fail "a program that really repaints must still be flagged: $(cat "$TMP/paint.err")"

$TD stop -n td30 --socket "$SOCK" >/dev/null
echo "E2E-30 PASS"
