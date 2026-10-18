#!/usr/bin/env bash
# E2E-18: context economy — every observation says what it costs an agent's
# context, warns when a capture is big and names the way out (`clear`), and
# `clear` actually reclaims the screen + scrollback (unmanaged panes too).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td18sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-18 FAIL: $*"; exit 1; }

$TD start -n td18 --cmd bash --socket "$SOCK" --width 80 --height 20 >/dev/null \
  || fail "start failed"
$TD wait -n td18 --shell-ready --timeout 10 >/dev/null || fail "shell not ready"

# --- a small capture costs little and must not nag ---
$TD send -n td18 --type "echo SMALL_18" --key Enter >/dev/null
$TD wait -n td18 --until 'SMALL_18' --timeout 5 >/dev/null || fail "no echo"
$TD screen -n td18 2>"$TMP/quiet.err" >/dev/null
if grep -qi 'budget' "$TMP/quiet.err"; then
  fail "a small screen must not warn about context: $(cat "$TMP/quiet.err")"
fi

# --- a big capture reports its cost, says it is over budget, names clear ---
$TD send -n td18 --type "seq -f 'row %g padding padding padding padding padding' 1 300" --key Enter >/dev/null
$TD wait -n td18 --cmd-done --timeout 20 >/dev/null || fail "seq never finished"
$TD screen -n td18 --scrollback 400 2>"$TMP/big.err" >/dev/null
grep -Eqi 'tokens' "$TMP/big.err" || fail "big capture must state a token cost: $(cat "$TMP/big.err")"
grep -Eqi 'budget' "$TMP/big.err" || fail "big capture must flag the budget: $(cat "$TMP/big.err")"
grep -Eqi 'clear'  "$TMP/big.err" || fail "the warning must name the way out (clear): $(cat "$TMP/big.err")"

# --- structured channels carry the same numbers as data, not prose ---
$TD screen -n td18 --scrollback 400 --grid 2>/dev/null | python3 -c '
import json, sys
g = json.load(sys.stdin)
' || fail "grid still valid"
$TD screen -n td18 --meta | python3 -c '
import json, sys
m = json.load(sys.stdin)
c = m["context"]
assert c["est_tokens"] > 0 and c["bytes"] > 0, m
assert "budget" in c and isinstance(c["over_budget"], bool), m
' || fail "--context block missing from --meta"

$TD wait -n td18 --until 'row 300' --timeout 5 | python3 -c '
import json, sys
v = json.load(sys.stdin)
assert v["context"]["est_tokens"] >= 0, v
' || fail "wait verdict must report its context cost"

# --- clear reclaims the scrollback and the visible output ---
before=$($TD screen -n td18 --meta | python3 -c 'import json,sys; print(json.load(sys.stdin)["history_size"])')
[ "$before" -gt 100 ] || fail "expected a fat scrollback to clear, got $before"
$TD clear -n td18 >"$TMP/clear.json" || fail "clear failed"
$TD screen -n td18 --meta | python3 -c '
import json, sys
m = json.load(sys.stdin)
assert m["history_size"] == 0, "scrollback not reclaimed: %s" % m["history_size"]
' || fail "clear did not reclaim history"
$TD screen -n td18 2>/dev/null | grep -q 'row 300' \
  && fail "clear left the old output on the visible screen"
python3 -c "
import json
r = json.load(open('$TMP/clear.json'))
assert r['ok'] and r['rows_after'] < r['rows_before'], r
assert r['history_size'] == 0, r
" || fail "clear report shape"

# --- --history-only never touches the program's input ---
$TD send -n td18 --type "seq -f 'again %g padding padding padding' 1 200" --key Enter >/dev/null
$TD wait -n td18 --cmd-done --timeout 20 >/dev/null || fail "seq 2 never finished"
$TD clear -n td18 --history-only >/dev/null || fail "--history-only failed"
$TD screen -n td18 --meta | python3 -c '
import json, sys
m = json.load(sys.stdin)
assert m["history_size"] == 0, m
' || fail "--history-only did not drop the scrollback"
$TD screen -n td18 2>/dev/null | grep -q 'again 200' \
  || fail "--history-only must leave the visible screen alone"

# --- a --history-only that reclaims nothing must say so ----------------------
# A short pane has no scrollback: `--history-only` can answer
# `rows_before == rows_after` and `history_size: 0`, and those counts must not
# read as work done. A no-op has to be distinguishable from a success, because
# the whole point of the command is the cost of the NEXT capture.
$TD stop -n td18 --socket "$SOCK" >/dev/null || fail "stop failed"
$TD start -n td18b --cmd bash --socket "$SOCK" --width 80 --height 24 >/dev/null
$TD wait -n td18b --shell-ready --socket "$SOCK" --timeout 15 >/dev/null
$TD send -n td18b --type "echo SHORT_PANE" --key Enter --socket "$SOCK" >/dev/null
$TD wait -n td18b --cmd-done --socket "$SOCK" --timeout 15 >/dev/null
$TD clear -n td18b --history-only --socket "$SOCK" | python3 -c '
import json, sys
r = json.load(sys.stdin)
n = r.get("note") or ""
assert "nothing was reclaimed" in n and "no scrollback" in n, \
    f"a no-op clear must be named as one: {r}"
' || fail "silent no-op clear (see above)"

# --- clear works on an unmanaged pane too (no state.json needed) ---
tmux -L "$SOCK" new-session -d -s stray 'echo STRAY; sleep 60' >/dev/null 2>&1 \
  || fail "stray session failed"
sleep 0.5
$TD clear -n stray --socket "$SOCK" >/dev/null || fail "clear must work on unmanaged panes"

# --- the image channel states its cost too ---
if python3 -c 'import PIL' 2>/dev/null; then
  $TD start -n td18big --cmd bash --socket "$SOCK" --width 200 --height 60 >/dev/null \
    || fail "start big failed"
  $TD wait -n td18big --shell-ready --timeout 10 >/dev/null
  $TD send -n td18big --type "seq 1 60" --key Enter >/dev/null
  $TD wait -n td18big --cmd-done --timeout 10 >/dev/null || true
  $TD screenshot -n td18big -o "$TMP/big.png" 2>"$TMP/img.err" >"$TMP/img.json" \
    || fail "screenshot failed"
  python3 -c "
import json, os
r = json.load(open('$TMP/img.json'))
c = r['context']
assert c['image_bytes'] == os.path.getsize(r['path']), (c, r)
assert c['est_tokens'] > 0, c
assert c['over_budget'] is True, f'a 200x60 pane image is a large context buy: {c}'
" || fail "screenshot context block (see above)"
  grep -Eqi 'clear|budget' "$TMP/img.err" || fail "over-budget image must warn on stderr"
  $TD stop -n td18big >/dev/null
fi

$TD stop -n td18 >/dev/null
echo "E2E-18 PASS"
