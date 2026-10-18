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
# idles — no prompt text to shift the geometry. The symbol characters are written
# as literal UTF-8: dash's builtin printf does not understand \uXXXX (it prints
# the escape text), which would silently test the wrong thing.
cat > "$TMP/paint.sh" <<'SH'
#!/bin/bash
printf '\e[38;2;255;0;0m▄▀█\e[0m \e[38;2;0;255;0m┌─┐\e[0m \ue0b0 \U0001F600 done ⣿⠀\n'
sleep 120
SH
chmod +x "$TMP/paint.sh"
$TD start -n td22 --socket "$SOCK" --width 60 --height 12 \
  --cmd "bash $TMP/paint.sh" >/dev/null || fail "start failed"
$TD wait -n td22 --until 'done' --timeout 10 >/dev/null || fail "target never painted"

$TD screenshot -n td22 -o "$TMP/g.png" >"$TMP/g.json" || fail "screenshot failed"

python3 - "$TMP/g.json" "$TMP/g.png" <<'PY' || fail "fidelity assertions (see above)"
import json, sys
from PIL import Image
r = json.load(open(sys.argv[1]))
im = Image.open(sys.argv[2])
assert r["palette"] is True, "a terminal screen has <256 colors: expect the exact palette PNG"
im = im.convert("RGB")   # palette PNG: compare the colors it decodes to, not indexes
cw, lh = r["charw"], r["lineh"]
g = r["glyphs"]
assert set(g) >= {"cells", "vector", "primary", "fallback", "missing"}, g
assert g["cells"] > 0, g
assert g["vector"] >= 8, f"blocks+box+powerline+braille must be drawn, not fonted: {g}"
# the PUA separator must not be counted as a resolved glyph it cannot render
print(f"glyph sources: {g}")

def px(x, y):
    return im.getpixel((x, y))[:3]

RED = (255, 0, 0)
GREEN = (0, 255, 0)
BG = (0, 0, 0)
# cells 0,1,2: ▄ (lower half) ▀ (upper half) █ (full) in truecolor red
assert px(cw // 2, lh - 2) == RED, f"lower half of the ▄ cell: {px(cw//2, lh-2)}"
assert px(cw // 2, 2) != RED, f"upper half of the ▄ cell must stay background: {px(cw//2, 2)}"
assert px(cw + cw // 2, 2) == RED, "upper half of the ▀ cell"
assert px(cw + cw // 2, lh - 2) != RED, "lower half of the ▀ cell must stay bg"
assert px(2 * cw + cw // 2, lh // 2) == RED, "the █ cell must be fully red"
# cell 3 is a space: nothing painted there
assert all(px(3 * cw + i, lh // 2) == BG for i in range(0, cw, max(1, cw // 4))), "space cell got ink"
# cells 4..6: green box drawing ┌─┐. The stems run from the cell *centre*
# outward (U+250C is "down and right"), so the corner cells have ink below the
# midline and none above it.
midrow = lh // 2
assert px(5 * cw + cw // 2, midrow) == GREEN, "the ─ between the corners is missing"
assert px(4 * cw + cw // 2, lh - 3) == GREEN, "┌ must run down from the centre"
assert px(6 * cw + cw // 2, lh - 3) == GREEN, "┐ must run down from the centre"
assert px(4 * cw + cw // 2, 2) == BG, "nothing above the centre in a ┌ cell"
# cell 8: U+E0B0 powerline separator — drawn geometrically, in the default fg
pl = [px(8 * cw + i, lh // 2) for i in range(0, cw, max(1, cw // 4))]
assert any(p != BG for p in pl), f"powerline separator rendered empty: {pl}"
# cells 18,19: ⣿ (all eight dots) and ⠀ (no dots) — braille spinners and
# sparklines have no glyphs in a UI mono, so they must come out as geometry
dot_tl = px(18 * cw + cw // 4, lh // 8)          # dot 1: top of the left column
dot_br = px(18 * cw + 3 * cw // 4, 7 * lh // 8)  # dot 8: bottom of the right
assert dot_tl != BG and dot_br != BG, f"⣿ dots missing: {dot_tl} {dot_br}"
empty = [px(19 * cw + i, y) for i in range(0, cw, max(1, cw // 4))
         for y in (lh // 8, lh // 2, 7 * lh // 8)]
assert all(p == BG for p in empty), f"⠀ must stay empty: {set(empty)}"
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

# --- CJK through the fallback chain, and arrows as geometry (issue #3) -------
# The reporter's device had no CJK/arrow coverage in its primary face at all;
# this box had a different failure: seven `unifont*` files filled the four
# fallback slots, so the real CJK face further down the listing was never opened
# and the picture came out as an 8x16 bitmap. Both shapes are pinned here — the
# chain must be chosen by *coverage*, and a proper face must beat the bitmap when
# the machine has one.
#
# The arrows go on their own line: a CJK glyph owns two columns, so cells after
# it on the same row would shift, and a pixel assertion would be testing the
# layout instead of the glyph.
cat > "$TMP/paint_cjk.sh" <<'SH'
#!/bin/bash
printf '\e[38;2;0;255;0m← →\e[0m\n你好世界\n'
sleep 120
SH
chmod +x "$TMP/paint_cjk.sh"
$TD start -n td22cjk --socket "$SOCK" --width 60 --height 10 \
  --cmd "bash $TMP/paint_cjk.sh" >/dev/null || fail "start (cjk) failed"
$TD wait -n td22cjk --until '你好世界' --timeout 10 >/dev/null || fail "cjk target never painted"
$TD screenshot -n td22cjk -o "$TMP/cjk.png" >"$TMP/cjk.json" \
  || fail "screenshot (cjk) failed"

python3 - "$TMP/cjk.json" "$TMP/cjk.png" <<'PY' || fail "CJK/arrow fidelity (see above)"
import json, os, sys
from PIL import Image
from termuse import screenshot
r = json.load(open(sys.argv[1]))
g = r["glyphs"]
im = Image.open(sys.argv[2]).convert("RGB")
cw, lh = r["charw"], r["lineh"]
GREEN, BG = (0, 255, 0), (0, 0, 0)

def px(x, y):
    return im.getpixel((x, y))[:3]

assert g["vector"] >= 2, f"the arrow range must be drawn, not hoped for: {g}"
# row 0: ← in cell 0, → in cell 2 (cell 1 is the space between them)
mid = lh // 2
for cell, head_left in ((0, True), (2, False)):
    x0 = cell * cw
    shaft = [px(x0 + i, mid) for i in range(1, cw - 1, max(1, cw // 6))]
    assert any(p == GREEN for p in shaft), f"arrow in cell {cell} has no shaft: {shaft}"
    edge = px(x0 + 1 if head_left else x0 + cw - 2, mid)
    assert edge == GREEN, f"arrow head of cell {cell} not at the edge: {edge}"
    opposite = [px(x0 + i, y) for i in (1, cw - 2) for y in (2, lh - 3)]
    assert all(p == BG for p in opposite), \
        f"cell {cell} has ink where a horizontal arrow cannot reach: {opposite}"
# row 1: CJK must have ink in the *second* row, from a real face, not a box
cjk_row = [px(x, lh + mid) for x in range(0, 5 * cw, max(1, cw // 3))]
assert any(p != BG for p in cjk_row), f"CJK cells came out blank: {set(cjk_row)}"

chosen = [os.path.basename(f).lower() for f in g.get("fallback_fonts", [])]
cjk_hints = ("wqy", "zenhei", "noto", "cjk", "droid", "source-han", "ipa",
             "wenquanyi")
installed = any(h in os.path.basename(p).lower()
                for p in screenshot._candidate_paths(screenshot.font_dirs())
                for h in cjk_hints)
if installed:
    assert chosen and not all("unifont" in f for f in chosen), \
        f"a real CJK face exists and the chain still picked the bitmap: {chosen}"
    assert g["missing"] == 0, f"this box can draw CJK and reported tofu: {g}"
else:
    assert g["missing"] >= 4, \
        f"no CJK face here, so the honest answer is a reported gap: {g}"
print(f"cjk chain: {chosen}  glyphs={ {k: g[k] for k in ('vector','primary','fallback','missing')} }")
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
