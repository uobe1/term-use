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

# --- a flag that cannot apply is refused, not silently dropped -------------
# Each of these used to be accepted and then never read: the agent got half an
# answer with no signal that the other half was thrown away, which is the worst
# kind of silence for a tool whose product is evidence.
refuses() {   # $1 = what must be refused, rest = the command
  local what="$1"; shift
  if "$@" >/dev/null 2>"$TMP/refuse.json"; then
    fail "$what must not be accepted silently"
  fi
  python3 -c "
import json
e = json.load(open('$TMP/refuse.json'))['error']
assert e['code'] == 'invalid-arguments', (e, '$what')
assert e['hint'], f'$what: a refusal that cannot say what to do instead is noise'
" || fail "$what (see above)"
}
refuses "screen --meta --grid"      $TD screen -n td17 --meta --grid
refuses "screen --meta -J"          $TD screen -n td17 --meta -J
refuses "wait --shell-ready --expect-code" \
        $TD wait -n td17 --shell-ready --expect-code 1 --timeout 3
refuses "wait --done --scrollback"  $TD wait -n td17 --done --scrollback 50 --timeout 3
refuses "replay -o with --format text" \
        $TD replay -n td17 --at last-send --format text -o "$TMP/x.png"

# a probe cannot answer a specific exit code: --done falls back to the `true`
# probe only when nothing has been sent, so this needs a session of its own
$TD start -n td17c --cmd bash --socket "$SOCK" >/dev/null || fail "start td17c"
refuses "--done probe + --expect-code" \
        $TD wait -n td17c --done --expect-code 5 --timeout 3
$TD stop -n td17c >/dev/null

# --- --version lives in the unit tests (this file skips without Pillow) -----

$TD stop -n td17 >/dev/null
echo "E2E-17 PASS"
