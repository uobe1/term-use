#!/usr/bin/env bash
# E2E-16: blank screen is a state, not a failure — screenshot must still render
# (at true pane geometry), the text channel must say "this is empty" instead of
# returning silent whitespace, and an un-executed input line must be visible.
# (Tester feedback: `screenshot` on a cleared pane died with
#  ValueError: cannot write empty image.)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td16sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"   # never depend on the caller's default socket

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-16 FAIL: $*"; exit 1; }

if ! python3 -c 'import PIL' 2>/dev/null; then
  echo "E2E-16 SKIP: image channel needs Pillow (uv pip install pillow)"
  exit 0
fi

W=100
H=30

# --- A pane that has never painted anything (daemon-like target) ---
$TD start -n td16q --cmd "sleep 600" --socket "$SOCK" --width $W --height $H >/dev/null \
  || fail "start failed"
sleep 1

# The screenshot of a blank pane must succeed and be pane-shaped, not content-cropped.
$TD screenshot -n td16q -o "$TMP/blank.png" >"$TMP/blank.json" \
  || fail "screenshot of a blank pane must not fail"
python3 - "$TMP/blank.json" "$W" "$H" <<'PY' || fail "blank png assertions (see above)"
import json, os, sys
from PIL import Image
r = json.load(open(sys.argv[1]))
W, H = int(sys.argv[2]), int(sys.argv[3])
assert r.get("blank") is True, f"must declare the screen blank: {r}"
assert r["grid"] == [W, H], f"image must be pane-shaped {W}x{H} cells, got {r['grid']}"
im = Image.open(r["path"])
assert im.format == "PNG", im.format
assert list(im.size) == r["size"], (im.size, r["size"])
assert im.size[0] == W * r["charw"] and im.size[1] == H * r["lineh"], r
assert os.path.getsize(r["path"]) > 100, "blank png must still be a real file"
PY

# --- The text channel must distinguish "empty" from "I failed to read" ---
$TD screen -n td16q --meta | python3 -c '
import json, sys
m = json.load(sys.stdin)
assert m.get("blank") is True, f"--meta must report the blank screen: {m}"
' || fail "--meta did not report blank"
$TD screen -n td16q 2>"$TMP/note.txt" >/dev/null
grep -Eiq '(empty screen|blank)' "$TMP/note.txt" \
  || fail "plain screen of a blank pane must say so on stderr, got: $(cat "$TMP/note.txt")"

# --- A bash session: clear paints nothing, then output makes it non-blank ---
$TD start -n td16b --cmd bash --socket "$SOCK" --width $W --height $H >/dev/null \
  || fail "start bash failed"
$TD wait -n td16b --shell-ready --timeout 10 >/dev/null || fail "shell never ready"
$TD send -n td16b --type "clear" --key Enter >/dev/null || fail "send clear failed"
$TD wait -n td16b --quiet-ms 400 --timeout 5 >/dev/null || true
$TD screenshot -n td16b -o "$TMP/cleared.png" >"$TMP/cleared.json" \
  || fail "screenshot after clear failed"
python3 -c "
import json
r = json.load(open('$TMP/cleared.json'))
assert r['grid'] == [$W, $H], f\"cleared shot must be pane-shaped: {r['grid']}\"
" || fail "cleared geometry"

$TD send -n td16b --type "echo NOW_I_HAVE_TEXT" --key Enter >/dev/null
$TD wait -n td16b --until 'NOW_I_HAVE_TEXT' --timeout 5 >/dev/null \
  || fail "output never appeared"
$TD screen -n td16b --meta | python3 -c '
import json, sys
m = json.load(sys.stdin)
assert m.get("blank") is False, f"screen has content now: {m}"
' || fail "--meta still claims blank after output"
$TD screenshot -n td16b -o "$TMP/filled.png" >"$TMP/filled.json" || fail "filled shot failed"
python3 -c "
import json
r = json.load(open('$TMP/filled.json'))
assert r.get('blank') is False, r
assert r['nonblank_rows'] >= 1, r
" || fail "filled blank-flag"

# --- Feedback #2: typed-but-un-executed input must be visible, not guessed ---
$TD send -n td16b --type "echo HALF_TYP" >/dev/null   # NOTE: no Enter
$TD wait -n td16b --quiet-ms 400 --timeout 5 >/dev/null || true
$TD screen -n td16b --meta | python3 -c '
import json, sys
m = json.load(sys.stdin)
assert m.get("unexecuted_input") == "echo HALF_TYP", f"un-executed line invisible: {m}"
assert "last_output_age_s" in m, f"no output-age signal: {m}"
' || fail "unexecuted input not reported (see assertion above)"

$TD send -n td16b --key Enter >/dev/null
$TD wait -n td16b --cmd-done --expect-code 0 --timeout 5 >/dev/null || true
$TD screen -n td16b --meta | python3 -c '
import json, sys
m = json.load(sys.stdin)
assert m.get("unexecuted_input") in (None, ""), f"cleared line still pending: {m}"
' || fail "unexecuted input not cleared after Enter"

$TD stop -n td16q >/dev/null; $TD stop -n td16b >/dev/null
echo "E2E-16 PASS"
