#!/usr/bin/env bash
# E2E-24: the recording must contain the target's FIRST bytes.
#
# raw.log is sold as the ground truth for protocol-level bytes, so a session
# that loses the opening output lies exactly where a reproduction begins. The
# recorder used to attach after the program started — a program that printed
# immediately could have its first line never recorded (observed as an
# intermittent failure). Repeated starts, because the bug was a race.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td24sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-24 FAIL: $*"; exit 1; }

for i in 1 2 3 4 5; do
  NAME="td24_$i"
  $TD start -n "$NAME" --cmd "printf 'FIRST_BYTES_24_$i\n'; sleep 20" \
    --socket "$SOCK" --width 50 --height 8 >/dev/null || fail "start $i failed"
  # out --all reads raw.log: if the recorder attached late, the line is simply
  # not there, and no screen read can recover it.
  if ! $TD out -n "$NAME" --all | grep -q "FIRST_BYTES_24_$i"; then
    fail "run $i: the target's first output is missing from the recording"
  fi
  $TD stop -n "$NAME" >/dev/null
done

# the release keystroke must not paint anything into the pane.
# (A script file, not `printf ...; sleep`: cmd_start execs the command, so a
# trailing `; sleep` would never run and the pane would die before we look.)
cat > "$TMP/once.sh" <<'SH'
#!/bin/sh
printf 'SECOND_LINE\n'
sleep 20
SH
chmod +x "$TMP/once.sh"
$TD start -n td24v --cmd "sh $TMP/once.sh" --socket "$SOCK" \
  --width 50 --height 8 >/dev/null || fail "start failed"
sleep 0.5
lines=$($TD screen -n td24v --socket "$SOCK" --meta | python3 -c 'import json,sys; print(json.load(sys.stdin)["nonblank_rows"])')
[ "$lines" -le 1 ] || fail "the gate release left junk on screen ($lines non-blank rows)"
first=$($TD screen -n td24v --socket "$SOCK" | head -1)
case "$first" in
  SECOND_LINE*) : ;;
  *) fail "the first screen row must be the program's output, got: [$first]" ;;
esac
$TD stop -n td24v >/dev/null

# start must report the recorder honestly
$TD start -n td24r --cmd bash --socket "$SOCK" --width 40 --height 6 \
  | python3 -c '
import json, sys
r = json.load(sys.stdin)
assert r["recorder"] is True, f"a working recorder must be reported: {r}"
' || fail "start recorder status"
$TD stop -n td24r >/dev/null

echo "E2E-24 PASS"
