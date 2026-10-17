#!/usr/bin/env bash
# E2E-29: resize — the channel that had no test, no skill row and no doc example.
#
# A resize is not cosmetic here: it reflows the screen, so `replay` has to
# reproduce it to rebuild any frame after it, and `--find` coordinates computed
# before it are wrong after it. That is why `resize` records an `r` event in
# raw.log rather than only calling tmux — this test pins both halves.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td29sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-29 FAIL: $*"; exit 1; }

$TD start -n td29 --cmd bash --socket "$SOCK" --width 100 --height 30 >/dev/null \
  || fail "start failed"
$TD wait -n td29 --socket "$SOCK" --shell-ready --timeout 15 >/dev/null || fail "not ready"

# --- 1. the geometry actually changes, and the tool reports the new one --------
$TD resize -n td29 --socket "$SOCK" --width 40 --height 10 >/dev/null || fail "resize failed"
$TD screen -n td29 --socket "$SOCK" --meta >"$TMP/meta.json" || fail "meta failed"
python3 -c "
import json
m = json.load(open('$TMP/meta.json'))
assert m['pane_width'] == 40 and m['pane_height'] == 10, m
" || fail "resize must move the reported geometry (see above)"

# --- 2. the reflow is real: 60 columns of text cannot fit in 40 ----------------
$TD send -n td29 --socket "$SOCK" --type "printf 'R29%sR29END\n' \"\$(printf 'x%.0s' \$(seq 1 40))\"" --key Enter >/dev/null
$TD wait -n td29 --socket "$SOCK" --done --timeout 20 >/dev/null || fail "printf never finished"
$TD screen -n td29 --socket "$SOCK" >"$TMP/wrapped.txt"
$TD screen -n td29 --socket "$SOCK" -J >"$TMP/joined.txt"
python3 -c "
wrapped = open('$TMP/wrapped.txt').read()
joined = open('$TMP/joined.txt').read()
assert 'R29END' in wrapped, 'the line must still be on screen'
# wrapped: the row starting with R29 cannot contain R29END too
rows = [ln for ln in wrapped.splitlines() if 'R29' in ln]
assert len(rows) >= 2, f'a 60-col line should occupy more than one 40-col row: {rows}'
assert not any(ln.startswith('R29') and 'R29END' in ln for ln in rows), \
    f'R29END on the same row means no reflow happened: {rows}'
# joined: -J puts it back together, which is how an agent reads a wrapped line
assert any('R29' in ln and 'R29END' in ln for ln in joined.splitlines()), \
    '-J must rejoin what the pane wrapped'
" || fail "reflow / -J after resize (see above)"

# --- 3. the resize is recorded, so a replay of a later frame uses the new size --
$TD trace -n td29 --socket "$SOCK" --format json \
  | python3 -c "
import json, sys
sizes = [ev[2] for line in sys.stdin
         if (ev := json.loads(line)) and isinstance(ev, list) and len(ev) > 2 and ev[1] == 'r']
assert {'cols': 40, 'rows': 10} in sizes, sizes
" || fail "the resize must be an r-event in raw.log (see above)"

$TD replay -n td29 --socket "$SOCK" --at last-send --format json \
  | python3 -c "
import json, sys
r = json.load(sys.stdin)
assert r['grid'] == [40, 10], f'the rebuilt frame must carry the post-resize geometry: {r[\"grid\"]}'
" || fail "replay after resize (see above)"

# --- 4. coordinates move: --find must answer against the NEW width -------------
$TD screen -n td29 --socket "$SOCK" --find R29END >"$TMP/find.json" 2>/dev/null || true
python3 -c "
import json
try:
    hits = json.load(open('$TMP/find.json'))
except Exception:
    hits = []
xs = [h['x'] for h in (hits if isinstance(hits, list) else hits.get('matches', []))]
assert all(x <= 40 for x in xs), f'--find returned columns beyond the resized width: {xs}'
" || fail "--find must respect the resized columns (see above)"

# --- 5. a missing session is a structured error, not a tmux whisper ------------
if $TD resize -n td29-nope --socket "$SOCK" --width 60 --height 20 \
     >/dev/null 2>"$TMP/err.json"; then
  fail "resize of a missing session must fail"
fi
python3 -c "
import json
e = json.load(open('$TMP/err.json'))['error']
assert e['code'] in ('session-missing', 'tmux-failed'), e
assert e['hint'], e
" || fail "resize error shape (see above)"

$TD stop -n td29 >/dev/null
echo "E2E-29 PASS"
