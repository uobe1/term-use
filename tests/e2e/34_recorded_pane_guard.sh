#!/usr/bin/env bash
# E2E-34: recorded evidence answers only for the pane it describes.
#
# `start` records the pane it made (`%N`), while `-n NAME:1.0` addresses
# whichever pane the caller names. raw.log, last_send_offset and the markers
# belong to the recorded pane, so a command that reads them must refuse the
# address instead of answering from another pane's bytes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
NAME="td34_$$_$RANDOM"
SOCK="td34sock_$$_$RANDOM"
TMP="$(mktemp -d)"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"
export XDG_STATE_HOME="$TMP/state"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-34 FAIL: $*"; exit 1; }

$TD start -n "$NAME" --cmd bash --socket "$SOCK" --width 80 --height 20 >/dev/null \
  || fail "start failed"
$TD wait -n "$NAME" --socket "$SOCK" --shell-ready --timeout 15 >/dev/null \
  || fail "shell never became ready"
$TD send -n "$NAME" --socket "$SOCK" --type "echo TD34_MAIN" --key Enter >/dev/null \
  || fail "send failed"
$TD wait -n "$NAME" --socket "$SOCK" --done --timeout 15 >/dev/null \
  || fail "the recorded command never finished"
tmux -L "$SOCK" new-window -t "$NAME" >/dev/null || fail "new-window failed"

refuse() {   # description, then the command
  local desc="$1"; shift
  local out rc
  set +e
  out="$($TD "$@" 2>&1)"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    echo "---- $out"
    fail "$desc answered for a pane the records do not describe"
  fi
  printf '%s\n' "$out" | python3 -c "
import json, sys
e = json.loads(sys.stdin.read().strip().splitlines()[-1])['error']
assert e['code'] == 'unmanaged-pane', e
ev = e.get('evidence') or {}
assert ev.get('addressed_pane') and ev.get('recorded_pane'), e
assert ev['addressed_pane'] != ev['recorded_pane'], e
" || { echo "---- $out"; fail "$desc refused for the wrong reason"; }
}

refuse "send" send -n "$NAME:1.0" --socket "$SOCK" --type x --key Enter
refuse "wait" wait -n "$NAME:1.0" --socket "$SOCK" --until READY --timeout 1
refuse "out" out -n "$NAME:1.0"
refuse "trace" trace -n "$NAME:1.0"
refuse "replay" replay -n "$NAME:1.0" --at last-send
refuse "clear" clear -n "$NAME:1.0" --socket "$SOCK" --history-only
refuse "screen --meta" screen -n "$NAME:1.0" --socket "$SOCK" --meta
refuse "mouse-detect" mouse-detect -n "$NAME:1.0" --socket "$SOCK"
refuse "fix-tty" fix-tty -n "$NAME:1.0" --socket "$SOCK"

# A new window makes the session's *active* pane different from the recorded
# one. An action command addressed to the bare session must refuse rather than
# send there and record here; a recording read still describes the recording.
refuse "send (active pane moved)" send -n "$NAME" --socket "$SOCK" --type x --key Enter
refuse "screen --meta (active pane moved)" screen -n "$NAME" --socket "$SOCK" --meta

# A plain screen read is about the addressed pane, not about the recording.
$TD screen -n "$NAME:1.0" --socket "$SOCK" >/dev/null \
  || fail "plain screen on another pane must still work"

# ... and the recorded session itself still answers.
$TD out -n "$NAME" >"$TMP/out.txt" || fail "out on the recorded session failed"
grep -q TD34_MAIN "$TMP/out.txt" || fail "the recorded stream is missing"

echo "E2E-34 ok"
