#!/usr/bin/env bash
# E2E-22: image-channel fidelity for what modern TUIs actually paint —
# powerline separators (PUA), half-block art, box drawing, color, and an
# honest answer about glyphs the machine cannot render.
#
# Before this, every one of those was drawn with one plain mono font, so a
# starship/prompt1k prompt or a braille/half-block spinner came out as tofu,
# and the picture quietly lied about what the pane showed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td22sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-22 FAIL: $*"; exit 1; }
python3 -c 'import PIL' 2>/dev/null || { echo "E2E-22 SKIP: Pillow absent"; exit 0; }

# The target paints a known line at a known cell (row 0, column 1) and then
# idles — no prompt text to shift the geometry.
cat > "$TMP/paint.sh" <<'SH'
#!/bin/sh
printf '\e[38;2;255;0;0m▄▀█\ue0b0\e[0m \e[38;2;0;255;0m┌─┐\e[0m 😀 done\n'
sleep 120
SH
chmod +x "$TMP/paint.sh"
$TD start -n td22 --socket "$SOCK" --width 60 --height 12 \
  --cmd "sh $TMP/paint.sh" >/dev/null || fail "start failed"
$TD wait -n td22 --until 'done' --timeout 10 >/dev/null || fail "target never painted"

$TD screenshot -n td22 -o "$TMP/g.png" >"$TMP/g.json" || fail "screenshot failed"

python3 - "$TMP/g.json" "$TMP/g.png" <<'PY' || fail "fidelity assertions (see above)"
import json, sys
from PIL import Image
r = json.load(open(sys.argv[1]))
im = Image.open(sys.argv[2])
cw, lh = r["charw"], r["lineh"]
g = r["glyphs"]
assert set(g) >= {"cells", "vector", "primary", "fallback", "missing"}, g
assert g["cells"] > 0, g
assert g["vector"] >= 7, f"blocks+box+powerline must be drawn, not fonted: {g}"
# the PUA separator must not be counted as a resolved glyph it cannot render
print(f"glyph sources: {g}")

def px(x, y):
    return im.getpixel((x, y))[:3]

RED = (255, 0, 0)
GREEN = (0, 255, 0)
# cell 0: U+2584 lower half block -> bottom half red, top half background
assert px(cw // 2, lh - 2) == RED, f"lower half of the ▄ cell: {px(cw//2, lh-2)}"
assert px(cw // 2, 2) != RED, f"upper half of the ▄ cell must be background: {px(cw//2, 2)}"
# cell 1: U+2580 upper half block -> top half red
assert px(cw + cw // 2, 2) == RED, "upper half of the ▀ cell"
# cell 2: U+2588 full block -> both halves red
assert px(2 * cw + cw // 2, lh // 2) == RED, "the █ cell must be fully red"
# cell 3: U+E0B0 powerline separator -> something was drawn (not tofu, not blank)
pl = [px(3 * cw + x, lh // 2) for x in range(0, cw, max(1, cw // 4))]
assert RED in pl, f"powerline separator rendered empty: {pl}"
# green box drawing on the next cells
assert any(px(x, 2) == GREEN for x in range(4 * cw, 9 * cw)), "box-drawing line missing"
PY

# --- honest about what the machine cannot render ---
python3 - "$TMP/g.json" <<'PY' || fail "missing-glyph reporting"
import json, sys
g = json.load(open(sys.argv[1]))["glyphs"]
assert isinstance(g["missing"], int)
if g["missing"]:
    samples = g["missing_samples"]
    assert samples and all(s["codepoint"].startswith("U+") for s in samples), samples
    print(f"unresolvable on this box: {samples}")
PY

# --- the file that was written matches what the JSON claims -----------------
python3 - "$TMP/g.json" "$TMP/g.png" <<'PY' || fail "reported size/path must match the file"
import json, os, sys
from PIL import Image
r = json.load(open(sys.argv[1]))
assert os.path.getsize(r["path"]) == r["context"]["image_bytes"], r["context"]
im = Image.open(sys.argv[2])
assert im.size[0] == r["size"][0] and im.size[1] == r["size"][1]
PY

$TD stop -n td22 >/dev/null 2>&1 || true
echo "E2E-22 PASS"
