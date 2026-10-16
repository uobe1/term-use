#!/usr/bin/env bash
# E2E-21: portability — the tool must work from the environment it is handed,
# not from one author's device layout.
#   (a) a broken TMUX_TMPDIR is a *structured* diagnosis, not a false
#       "session already exists" (tmux prints an error and exits 0 here)
#   (b) the pane gets the machine's own bash setup, found by convention
#   (c) fonts are found by convention too (explicit dir, $PREFIX, XDG, system)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td21sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-21 FAIL: $*"; exit 1; }

# --- (a) tmux's quiet failure must become a loud, actionable one ------------
: > "$TMP/not_a_directory"          # a FILE where tmux wants to build tmux-0/
if TMUX_TMPDIR="$TMP/not_a_directory" $TD start -n td21a --cmd bash \
     --socket "$SOCK" >/dev/null 2>"$TMP/su.json"; then
  fail "start with an unusable TMUX_TMPDIR must not succeed"
fi
python3 -c "
import json
e = json.load(open('$TMP/su.json'))['error']
assert e['code'] == 'socket-unreachable', e
assert 'TMUX_TMPDIR' in (e['hint'] or ''), f'the hint must name the knob: {e}'
" || fail "unusable TMUX_TMPDIR (see above)"

# --- (b) the user's own bash setup still applies inside a managed pane ------
mkdir -p "$TMP/home"
printf 'export TD_RC_MARK=seen\n' > "$TMP/home/.bashrc"
HOME="$TMP/home" $TD start -n td21b --cmd bash --socket "$SOCK" \
  --width 90 --height 25 >/dev/null || fail "start failed"
$TD wait -n td21b --shell-ready --timeout 10 >/dev/null \
  || fail "sourcing the user's rcfile must not break shell integration"
$TD send -n td21b --type 'echo MARK=$TD_RC_MARK' --key Enter >/dev/null
$TD wait -n td21b --until 'MARK=seen' --timeout 10 >/dev/null \
  || fail "the pane must see the user's own ~/.bashrc (generic sourcing)"
# ... and the fact oracle still answers afterwards
$TD send -n td21b --type "true" --key Enter >/dev/null
$TD wait -n td21b --done --expect-code 0 --timeout 10 >/dev/null \
  || fail "--done must still work after the user rcfile ran"

# --- (c) font discovery is env-driven, not device-driven --------------------
SYSFONT=$(find /usr/share/fonts -name "DejaVuSansMono*.ttf" 2>/dev/null | head -1)
[ -n "$SYSFONT" ] || { echo "E2E-21 SKIP: no system mono font to copy"; exit 0; }
python3 -c 'import PIL' 2>/dev/null \
  || { echo "E2E-21 SKIP(c): Pillow absent"; exit 0; }
mkdir -p "$TMP/fonts" "$TMP/prefix/share/fonts/mine"
cp "$SYSFONT" "$TMP/fonts/ProbeMono.ttf"
cp "$SYSFONT" "$TMP/prefix/share/fonts/mine/PrefixMono.ttf"
$TD send -n td21b --type "echo FONT_PROBE_21" --key Enter >/dev/null
$TD wait -n td21b --until 'FONT_PROBE_21' --timeout 5 >/dev/null

# explicit font dir wins over everything installed on the machine
TERM_USE_FONT_DIRS="$TMP/fonts" $TD screenshot -n td21b -o "$TMP/f1.png" \
  >"$TMP/f1.json" || fail "explicit TERM_USE_FONT_DIRS must work"
python3 -c "
import json
r = json.load(open('$TMP/f1.json'))
assert r['font'] == 'ProbeMono.ttf', f'my font dir must win: {r[\"font\"]}'
" || fail "TERM_USE_FONT_DIRS priority"

# ... and a prefix-style tree is just another font dir (the $PREFIX convention
# is what Termux/conda/Nix profiles all use; nothing here names a device)
TERM_USE_FONT_DIRS="$TMP/prefix/share/fonts" $TD screenshot -n td21b -o "$TMP/f2.png" \
  >"$TMP/f2.json" || fail "prefix font tree must render"
python3 -c "
import json
r = json.load(open('$TMP/f2.json'))
assert r['font'] == 'PrefixMono.ttf', r['font']
" || fail "prefix font tree"

# an explicit font dir *is* the search list: pointing it at nothing must give a
# structured font-missing (with the fix named), never a traceback or a silent lie
mkdir -p "$TMP/emptyfonts"
if TERM_USE_FONT_DIRS="$TMP/emptyfonts" $TD screenshot -n td21b -o "$TMP/f3.png" \
     >/dev/null 2>"$TMP/fm.json"; then
  fail "an empty explicit font dir must not silently succeed"
fi
python3 -c "
import json
e = json.load(open('$TMP/fm.json'))['error']
assert e['code'] == 'font-missing', e
assert 'TERM_USE_FONT' in (e['hint'] or ''), e
" || fail "empty font dir must give font-missing (see above)"

$TD stop -n td21b >/dev/null
echo "E2E-21 PASS"
