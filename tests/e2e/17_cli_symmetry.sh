#!/usr/bin/env bash
# E2E-17: observation subcommands must not punish reasonable guesses —
# `screenshot` without -o writes somewhere sane, `screen --text` is accepted
# (tester feedback: both surfaced as raw argparse errors).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td17sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-17 FAIL: $*"; exit 1; }
python3 -c 'import PIL' 2>/dev/null \
  || { echo "E2E-17 SKIP: Pillow not installed"; exit 0; }

$TD start -n td17 --cmd bash --socket "$SOCK" --width 80 --height 20 >/dev/null \
  || fail "start failed"
$TD wait -n td17 --shell-ready --timeout 10 >/dev/null || fail "shell not ready"
$TD send -n td17 --type "echo SYM_OK_17" --key Enter >/dev/null
$TD wait -n td17 --until 'SYM_OK_17' --timeout 5 >/dev/null || fail "no echo"

# --- screenshot without -o: must work and say where it wrote ---
$TD screenshot -n td17 >"$TMP/auto.json" || fail "screenshot without -o must not fail"
python3 -c "
import json, os
r = json.load(open('$TMP/auto.json'))
assert os.path.isfile(r['path']), f'no file at reported path: {r}'
assert os.path.getsize(r['path']) > 100, r
assert '$XDG_STATE_HOME' in r['path'], f'default path should live with the session records: {r[\"path\"]}'
" || fail "default screenshot path (see above)"

# --- -o and --out are the same thing; a second default capture must not
#     silently overwrite the first ---
$TD screenshot -n td17 -o "$TMP/a.png" >/dev/null || fail "-o failed"
$TD screenshot -n td17 --out "$TMP/b.png" >/dev/null || fail "--out failed"
[ -s "$TMP/a.png" ] && [ -s "$TMP/b.png" ] || fail "-o/--out produced no file"
$TD screenshot -n td17 >/dev/null 2>&1 || fail "second default screenshot failed"
n=$(ls "$XDG_STATE_HOME"/term-use/td17/*.png 2>/dev/null | wc -l)
[ "$n" -ge 2 ] || fail "default captures must not overwrite each other (saw $n)"

# --- screen --text: the explicit form of the default mode must be accepted ---
$TD screen -n td17 --text | grep -q SYM_OK_17 || fail "screen --text rejected"

$TD stop -n td17 >/dev/null
echo "E2E-17 PASS"
