#!/usr/bin/env bash
# E2E-19: column truth with wide characters.
#
# tmux does NOT emit a padding cell for the second half of a double-width
# character (grid_string_cells skips GRID_FLAG_PADDING), so a naive
# one-char-one-column reader silently drifts: every column index after 你好
# is off. That breaks `screen --find` -> `click` coordinates and renders the
# image at the wrong pitch — the screen is 18 columns for a 16-character
# capture, and term-use must know which of the two it is looking at.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td19sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-19 FAIL: $*"; exit 1; }

$TD start -n td19 --cmd bash --socket "$SOCK" --width 80 --height 20 >/dev/null \
  || fail "start failed"
$TD wait -n td19 --shell-ready --timeout 10 >/dev/null || fail "shell not ready"

# The command line is written with \x escapes so its *echo* cannot be mistaken
# for the output row — the only "abc" on screen is what printf painted.
$TD send -n td19 --type "printf '\\xe4\\xbd\\xa0\\xe5\\xa5\\xbd\\x61\\x62\\x63\\n'" --key Enter >/dev/null \
  || fail "send printf failed"
$TD wait -n td19 --until 'abc' --timeout 5 >/dev/null || fail "printf never ran"

# --- where does "abc" actually sit?  你=cols 1-2, 好=cols 3-4, a=col 5 ---
$TD screen -n td19 --find abc | python3 -c '
import json, sys
hits = json.load(sys.stdin)
assert len(hits) == 1, f"expected exactly one abc on screen: {hits}"
first = hits[0]
assert first["x"] == 5, f"wide chars drift the column index: {first}"
' || fail "--find must count screen columns, not characters (see above)"

# --- asking for the reserved half answers with the glyph that owns it ---
$TD screen -n td19 --find 你好 | python3 -c '
import json, sys
hits = json.load(sys.stdin)
assert hits and hits[0]["x"] == 1, hits
row = hits[0]["y"]
' || fail "--find should still match a double-width string"
ROW=$($TD screen -n td19 --find 你好 | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["y"])')
$TD screen -n td19 --element-at 1 "$((ROW - 1))" | python3 -c '
import json, sys
c = json.load(sys.stdin)
assert c["char"] == "你", f"column 2 belongs to 你: {c}"
' || fail "wide glyph half-cell lookup"

# --- cell widths are reported, so a reader can lay out without guessing ---
$TD screen -n td19 --grid | python3 -c '
import json, sys
grid = json.load(sys.stdin)
wide = [c for row in grid for c in row if c and c.get("char") in ("你", "好")]
assert wide, "the double-width glyphs must be in the grid"
for c in wide:
    assert c.get("w") == 2, f"wide glyph must declare 2 columns: {c}"
pads = [c for row in grid for c in row if c and c.get("pad")]
assert pads, "the reserved half of a wide glyph must be explicit"
for p in pads:
    assert p["char"] == "" and p["w"] == 0, p
# ordinary ASCII cells keep w=1
ascii = [c for row in grid for c in row if c and c.get("char") == "a"]
assert ascii and ascii[0]["w"] == 1, ascii[:1]
' || fail "grid must model wide cells (see above)"

# --- the image keeps its pitch: one column per screen cell ---
if python3 -c 'import PIL' 2>/dev/null; then
  $TD screenshot -n td19 -o "$TMP/w.png" >"$TMP/w.json" || fail "screenshot failed"
  python3 -c "
import json
r = json.load(open('$TMP/w.json'))
assert r['grid'][0] == 80, r['grid']
" || fail "screenshot must be pane-width"
fi

$TD stop -n td19 >/dev/null
echo "E2E-19 PASS"
