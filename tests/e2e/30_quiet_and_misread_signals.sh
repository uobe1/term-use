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
# The note must explain the empty answer AND name both readings, because tmux
# cannot tell "never started" from "a live server whose socket file was removed".
grep -qi 'no socket file' "$TMP/s.err" || fail "the empty answer must be explained on stderr: $(cat "$TMP/s.err")"
grep -qi 'either' "$TMP/s.err" && grep -qi 'kill-server\|start' "$TMP/s.err" \
  || fail "the note must cover both readings: $(cat "$TMP/s.err")"

$TD start -n td30 --cmd bash --socket "$SOCK" --width 90 --height 24 >/dev/null \
  || fail "start failed"
$TD wait -n td30 --socket "$SOCK" --shell-ready --timeout 15 >/dev/null \
  || fail "shell not ready"

# --- 1. quiet settles, but must not be misread as completion ---------------
# Durations are deliberately long: every CLI call on this box costs 0.5-3s of
# process startup, so a `sleep 4` under test can finish while the test is still
# between two waits — which is a bug in the test, not in the tool.
$TD send -n td30 --socket "$SOCK" --type "python3 -c 'import time; time.sleep(25)'" \
  --key Enter >/dev/null || fail "send failed"
sleep 1.5                       # the echo has landed; the program is now silent
$TD wait -n td30 --socket "$SOCK" --quiet-ms 700 --timeout 6 \
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
$TD wait -n td30 --socket "$SOCK" --done --expect-code 0 --timeout 35 \
  | python3 -c '
import json, sys
v = json.load(sys.stdin)
assert v["confidence"] == "fact" and v["exit_code"] == 0, v
assert "note" not in v, f"--done has nothing to caveat: {v}"
' || fail "--done must still prove completion after the silent stretch"

# --- 1b/1c. a timeout on a command that is still running must say so --------
# One wait, not two: chaining waits here turns the test's own process startup
# into a race against the program. The same command is then followed to its
# fact-level completion, so the sequence proves both halves of the claim.
$TD send -n td30 --socket "$SOCK" --type "python3 -c 'import time; time.sleep(12)'" \
  --key Enter >/dev/null || fail "send failed"
sleep 2
$TD wait -n td30 --socket "$SOCK" --done --timeout 2 2>"$TMP/mid.json" || true
python3 -c "
import json
e = json.load(open('$TMP/mid.json'))['error']
assert e['code'] == 'timeout', e['code']
assert e['evidence'].get('in_flight') is True, \
    'the evidence must state that a command is mid-flight: ' + json.dumps(e)[:200]
h = e['hint'] or ''
assert 'mid-flight' in h and 'loosen' in h, f'the hint must retract the --until advice: {h}'
" || fail "in-flight timeout (see above)"

# and the completion question is still answerable, at fact level
$TD wait -n td30 --socket "$SOCK" --done --expect-code 0 --timeout 25 >/dev/null \
  || fail "--done must reach fact once the silent command finishes"

# --- 1d. a compound line's one exit code must say which part it describes ---
# `false` then `echo`: the line exits 0, so the number is true and worthless —
# the only honest thing left is to name what the number covers. The case above
# (`python3 -c 'import time; …'`, no note) is the other half: a `;` inside
# quotes is data, and flagging it would make a complete fact look partial.
$TD send -n td30 --socket "$SOCK" --type 'false; echo TD30_COMPOUND' --key Enter >/dev/null
$TD wait -n td30 --socket "$SOCK" --done --timeout 15 >"$TMP/compound.json" \
  || fail "compound line never completed"
python3 -c "
import json
v = json.load(open('$TMP/compound.json'))
assert v['confidence'] == 'fact' and v['exit_code'] == 0, v
n = v.get('note') or ''
assert 'failed unseen' in n and 'echo TD30_COMPOUND' in n, \
    f'a semicolon line must be caveated where the number cannot be: {n!r}'
" || fail "compound-line scope (see above)"

# --- 1e. `clear` in a plain shell is not a program that repositions text -----
# A false redraw note makes an agent distrust a *correct* read and re-verify a
# non-idempotent program, so `clear` must not raise it.
$TD send -n td30 --socket "$SOCK" --type "clear; echo TD30_CLEARED" --key Enter >/dev/null
$TD wait -n td30 --socket "$SOCK" --done --timeout 15 >/dev/null \
  || fail "clear+echo never finished"
$TD out -n td30 --socket "$SOCK" --json | python3 -c '
import json, sys
r = json.load(sys.stdin)
assert "TD30_CLEARED" in r["text"], r
assert not r["redrawn"], "a shell that merely cleared must not be called a redraw"
assert r["scope"] == "since-send" and isinstance(r["since_offset"], int), r
' || fail "clear flagged as a redraw, or no window offset (see above)"

# --- 1f. --type-file: text carrying both quote styles, no shell guessing -----
cat > "$TMP/quoted.txt" <<'Q'
python3 -c "print('TD30_QUOTE_OK')"
Q
$TD send -n td30 --socket "$SOCK" --type-file "$TMP/quoted.txt" --key Enter >/dev/null
$TD wait -n td30 --socket "$SOCK" --done --expect-code 0 --timeout 15 >/dev/null \
  || fail "--type-file text never ran"
$TD out -n td30 --socket "$SOCK" --json | python3 -c '
import json, sys
r = json.load(sys.stdin)
assert "TD30_QUOTE_OK" in r["text"], r
assert "\"print('"'"'TD30_QUOTE_OK'"'"')\"" in r["text"], \
    "the pane must have received the quoting verbatim: " + r["text"]
' || fail "--type-file delivery (see above)"

# --- 1c. trace says when the session started, so elapsed maps to wall clock -
$TD trace -n td30 --socket "$SOCK" 2>/dev/null | head -1 | grep -q 'started=' \
  || fail "trace's header must carry the recording's start time (elapsed alone cannot be attributed to a file write)"

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

# --- 4. `sessions -n` answers "is my session there?" in one call ------------
$TD sessions -n td30 --socket "$SOCK" 2>/dev/null | python3 -c '
import json, sys
rows = json.load(sys.stdin)
assert len(rows) == 1 and rows[0]["name"].startswith("td30"), rows
' || fail "sessions -n must filter to the one session"
$TD sessions -n notthere --socket "$SOCK" 2>/dev/null | grep -q '^\[\]$' \
  || fail "sessions -n on an absent name is an empty list, not an error"

$TD stop -n td30 --socket "$SOCK" >/dev/null
echo "E2E-30 PASS"
