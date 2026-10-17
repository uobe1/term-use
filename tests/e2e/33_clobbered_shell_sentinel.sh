#!/usr/bin/env bash
# E2E-33: a shell whose own rc takes the prompt machinery back still answers facts.
#
# The device case: ~/.bashrc ends with a wrapper that clears PS1/PS0/
# PROMPT_COMMAND and reads lines in its own program, so term-use's injected
# integration is dead from the first prompt — the pane runs, and no OSC 133
# marker ever arrives. Every `--cmd-done`/`--shell-ready` there is unsatisfiable,
# and the whole e2e suite fails identically with and without this round's fixes.
#
# The answer is not to hope the theme cooperates: when the recent stream carries
# no markers, `send` appends a POSIX completion sentinel of its own, and the exit
# code reaches the recording as bytes term-use wrote — a fact, with its provenance
# stated. This asserts all four shapes:
#   1. the clobbered shell gets `fact` + the right code, and the line still runs;
#   2. a line our sentinel could not safely follow is refused, not silently
#      rewritten (`marker: skipped` + reason), and no fact is claimed for it;
#   3. a properly integrated shell is left completely alone (`marker: shell`, the
#      text sent is exactly what the caller typed);
#   4. `--shell-ready` works in the clobbered shell too (the probe carries its own
#      sentinel), which is the case that used to refuse at the gate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
NAME="td33_$$_$RANDOM"
SOCK="td33sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-33 FAIL: $*"; exit 1; }

# --- 1. the clobbered shell --------------------------------------------------
$TD start -n "$NAME" --cmd "bash --rcfile $ROOT/tests/fixtures/clobbering.bashrc -i" \
  --socket "$SOCK" --width 90 --height 25 >/dev/null || fail "start failed"
DIR="$XDG_STATE_HOME/term-use/$NAME"

OUT="$($TD send -n "$NAME" --type 'echo TU_$((3+4))' --key Enter)" || fail "send failed"
printf '%s\n' "$OUT" | python3 -c "
import json, sys
r = json.load(sys.stdin)
assert r['sent']['text'].startswith('echo TU_\$((3+4)); '), r
assert r['marker'] == 'sentinel', f'the clobbered shell must be tracked: {r}'
" || fail "the first send did not attach a sentinel (see above)"

$TD wait -n "$NAME" --done --timeout 15 >"$TMP/done.json" || fail "--done never answered in a clobbered shell"
python3 - "$TMP/done.json" "$DIR/state.json" <<'PY' || fail "the sentinel answer is not an honest fact"
import json
v = json.load(open(__import__("sys").argv[1]))
assert v["verdict"] == "met" and v["confidence"] == "fact", v
assert v.get("exit_code") == 0, v
# which server saw it depends on the tmux in front of us: our sentinel's markers
# are parsed by tmux 3.8 too, so either channel is a correct answer
assert v.get("via") in ("osc133", "tmux-command-status"), v
note = v.get("note") or ""
assert "sentinel" in note, f"the verdict must say the marker was synthesized: {note}"
assert "__tu_ec" not in note, \
    f"the note must name the caller's stage, not our own suffix: {note}"
st = json.load(open(__import__("sys").argv[2]))
assert st.get("marker_source") == "sentinel", st
assert st.get("shell_integration") is True, st  # the stream proved it, not the launch guess
PY

# The command still ran as typed — a sentinel that corrupts the line is worse
# than no sentinel at all.
$TD out -n "$NAME" >"$TMP/out.txt" 2>/dev/null || fail "out failed"
grep -q "TU_7" "$TMP/out.txt" || fail "the sent line was changed by our own suffix: $(cat "$TMP/out.txt")"

# --- 2. an unsafe line is refused, not rewritten -----------------------------
OUT="$($TD send -n "$NAME" --type "sleep 1 &" --key Enter)" || fail "send (background job)"
printf '%s\n' "$OUT" | python3 -c "
import json, sys
r = json.load(sys.stdin)
assert r['marker'] == 'skipped', r
assert 'background' in (r.get('marker_reason') or ''), r
assert r['sent']['text'] == 'sleep 1 &', f'the line must stay exactly as typed: {r}'
" || fail "a line the sentinel cannot follow was not refused cleanly (see above)"
set +e
BG="$($TD wait -n "$NAME" --done --timeout 3 2>&1)"
RC=$?
set -e
[ "$RC" != 0 ] || { echo "---- $BG"; fail "--done answered for a background job it could not observe"; }

# --- 3. a working integration is left alone ---------------------------------
$TD start -n td33ok --cmd bash --socket "$SOCK" --width 90 --height 25 >/dev/null \
  || fail "start (integrated) failed"
# Warm it up first: one prompt cycle must have run, so "the shell emits markers"
# is a reading of the stream and not a race with bash's startup.
$TD send -n td33ok --type "true" --key Enter >/dev/null || fail "warm-up send"
$TD wait -n td33ok --cmd-done --timeout 15 >/dev/null || fail "warm-up never finished"
$TD send -n td33ok --type "true" --key Enter >"$TMP/ok.json" || fail "send (integrated)"
python3 -c "
import json
r = json.load(open('$TMP/ok.json'))
assert r['marker'] == 'shell', f'a shell that emits markers must not be rewritten: {r}'
assert r['sent']['text'] == 'true', r
" || fail "the integrated session got a sentinel anyway (see above)"
$TD wait -n td33ok --done --timeout 15 >/dev/null || fail "integrated --done broke"

# --- 4. the probe works where the gate used to refuse ------------------------
$TD wait -n "$NAME" --shell-ready --timeout 15 >/dev/null \
  || fail "--shell-ready refused in the clobbered shell (probe must carry its own sentinel)"

# --- 5. the documented split send (type, then Enter) still gets its fact -----
# A half-typed line must not be lengthened, so the first send carries no marker;
# the Enter that submits it is the event that creates a completion, and it picks
# the pending line up from the recording and marks that.
S1="$($TD send -n "$NAME" --type 'echo TU_SPLIT')" || fail "send (text only)"
printf '%s\n' "$S1" | python3 -c "
import json, sys
r = json.load(sys.stdin)
assert 'marker' not in r, f'a line that was not submitted must not be marked: {r}'
" || fail "an unsubmitted line got a marker decision (see above)"
$TD send -n "$NAME" --key Enter >"$TMP/split.json" || fail "send (Enter only)"
python3 -c "
import json
r = json.load(open('$TMP/split.json'))
assert r['marker'] == 'sentinel', r
assert '__tu_ec' in r['sent']['text'], r
" || fail "the Enter that submitted the line did not bring the marker with it"
$TD wait -n "$NAME" --done --timeout 15 >/dev/null \
  || fail "--done could not answer after the split send"

# --- 6. a caller's own trailing `;` must not become `;;` --------------------
$TD send -n "$NAME" --type "cd $TMP; " --key Enter >"$TMP/semi.json" \
  || fail "send (semicolon)"
python3 -c "
import json
r = json.load(open('$TMP/semi.json'))
assert r['marker'] == 'sentinel', r
assert ';;' not in r['sent']['text'], r
" || fail "the suffix doubled the caller's semicolon (see above)"
$TD wait -n "$NAME" --done --timeout 15 >/dev/null \
  || fail "--done could not answer after a semicolon-terminated line"
$TD screen -n "$NAME" >"$TMP/scr.txt" 2>/dev/null || fail "screen failed"
if grep -qi "syntax error" "$TMP/scr.txt"; then
  fail "bash rejected our own suffix: $(cat "$TMP/scr.txt")"
fi

# --- 7. a program holding the tty is never fed our shell syntax -------------
# The bug this pins: `python3` in the foreground means the *next* line is program
# input, and appending `; __tu_ec=$?; printf …` produced a SyntaxError inside
# python on the device. The proof we use is our own: the last tracked line never
# reported back, so the shell is not reading.
$TD send -n "$NAME" --type "cat" --key Enter >/dev/null || fail "send (cat)"
sleep 0.4
OUT="$($TD send -n "$NAME" --type 'echo TU_INTO_CAT' --key Enter)" \
  || fail "send (into cat)"
printf '%s\n' "$OUT" | python3 -c "
import json, sys
r = json.load(sys.stdin)
assert r['marker'] == 'paused', r
assert r['sent']['text'] == 'echo TU_INTO_CAT', f'our suffix reached a program: {r}'
assert 'never reported back' in (r.get('marker_reason') or ''), r
" || fail "tracking did not pause for the program holding the tty (see above)"
$TD send -n "$NAME" --key C-d >/dev/null || fail "send (C-d)"
$TD wait -n "$NAME" --done --timeout 15 >/dev/null \
  || fail "the queued cat line never reported back once the program exited"
$TD send -n "$NAME" --type "echo TU_AFTER" --key Enter >"$TMP/resume.json" \
  || fail "send (after cat)"
python3 -c "
import json
r = json.load(open('$TMP/resume.json'))
assert r['marker'] == 'sentinel', f'tracking must resume once the marker arrives: {r}'
" || fail "tracking never resumed after the program left (see above)"
$TD wait -n "$NAME" --until 'TU_AFTER' --timeout 10 >/dev/null \
  || fail "the line after the program never ran"

echo "E2E-33 ok"
