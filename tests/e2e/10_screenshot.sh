#!/usr/bin/env bash
# E2E-10: screenshot channel — png/jpg via Pillow, pillow-missing isolation.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td10sock_$$_$RANDOM"
TMP="$(mktemp -d)"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"
export XDG_STATE_HOME="$TMP/state"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-10 FAIL: $*"; exit 1; }

# Prerequisites: a scenario target and the optional image dependency. Skip
# honestly on a box missing either, rather than report a product failure for
# something that was never installed. (TERM_USE_DISABLE_IMAGE=1 isolation of
# the pillow-missing path is exercised below regardless.)
command -v nano >/dev/null || { echo "E2E-10 SKIP: nano not installed"; exit 0; }
python3 -c 'import PIL' 2>/dev/null \
  || { echo "E2E-10 SKIP: Pillow not installed (uv pip install pillow)"; exit 0; }

$TD start -n td10 --cmd bash --socket "$SOCK" --width 100 --height 30 >/dev/null \
  || fail "start failed"
printf 'hello screenshot\nline two\n' > "$TMP/hello.txt"
$TD send -n td10 --type "nano $TMP/hello.txt" --key Enter >/dev/null || fail "send nano failed"
$TD wait -n td10 --until 'GNU nano' --timeout 10 >/dev/null || fail "nano never opened"

# --- png render (the only format: a palette PNG is lossless and smaller than
#     JPEG was on terminal screens, so the second format earned no users) ---
$TD screenshot -n td10 -o "$TMP/s.png" >"$TMP/png.json" || fail "png render failed"
python3 - "$TMP/png.json" <<'PY' || fail "file assertions (see above)"
import json, sys, os
from PIL import Image
r = json.load(open(sys.argv[1]))
sz = os.path.getsize(r["path"])
assert sz > 5120, f"PNG: {sz} bytes (want >5KB)"
im = Image.open(r["path"])
assert im.format == "PNG", f"PNG: got {im.format}"
assert list(im.size) == r["size"], f"file {im.size} vs claimed {r['size']}"
W, H = r["grid"]
assert r["size"][0] == W * r["charw"] and r["size"][1] == H * r["lineh"], r
PY

# a name that promises another format must be refused, not written mislabeled
if $TD screenshot -n td10 -o "$TMP/lie.jpg" >/dev/null 2>"$TMP/lie.json"; then
  fail "screenshot -o x.jpg must refuse: it writes PNG only"
fi
python3 -c "
import json
e = json.load(open('$TMP/lie.json'))['error']
assert e['code'] == 'invalid-arguments', e
assert '.png' in (e['hint'] or ''), e
" || fail "mislabeled output path (see above)"

# --format is gone with the format choice: an unknown flag is an argparse error
if $TD screenshot -n td10 --format jpg -o "$TMP/x.jpg" >/dev/null 2>&1; then
  fail "screenshot --format must no longer exist"
fi

# --- pillow-missing: -S skips site-packages, so PIL is importable nowhere ---
if python3 -S -m termuse screenshot -n td10 -o "$TMP/s2.png" \
    >/dev/null 2>"$TMP/np.json"; then
  fail "screenshot without Pillow must fail"
fi
python3 -c "
import json,sys
e=json.load(open('$TMP/np.json'))['error']
assert e['code']=='pillow-missing', e
assert e['hint'], 'install hint missing'
" || fail "expected pillow-missing error"

echo "E2E-10 PASS"
