#!/usr/bin/env bash
# E2E-31: a completion marker must be attributable to the send that caused it.
#
# `send` draws its window at the recording's current size, and the recorder lags
# the pane. Inject while the pane is still delivering bytes and the *previous*
# command's `C` and `D` both land inside the new window — a positional test then
# returns met / fact / exit 0 for a command that has not started. Measured on a
# loaded box two seconds into a `sleep 12`.
#
# What this pins, and where it cannot:
#   * always: the binding the fix uses (the echo of the sent line, found in a
#     real recording ahead of that command's own `C`), the per-send drain answer
#     in state.json, a met `fact` with the right code for our own completion, and
#     `expect-code-mismatch` failing fast;
#   * on a box that can stop a process: the borrowed-marker refusal itself,
#     produced by freezing the recorder between two sends. SIGSTOP is swallowed
#     under some sandboxes (measured here: a "stopped" process stays in state S),
#     and the test says so out loud instead of passing a section that ran nothing.
#     The refusal itself is pinned on every machine by
#     tests/unit/test_cmd_done_scope.py cd1/cd10 — both mutation-checked, meaning
#     deleting either half of the rule turns them red.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
NAME="td31_$$_$RANDOM"
SOCK="td31sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-31 FAIL: $*"; exit 1; }

$TD start -n "$NAME" --cmd bash --socket "$SOCK" --width 100 --height 30 >/dev/null \
  || fail "start failed"
DIR="$XDG_STATE_HOME/term-use/$NAME"

# Warm the shell once, so the markers we are testing are not the startup ones.
$TD send -n "$NAME" --type "true" --key Enter >/dev/null || fail "warm-up send"
$TD wait -n "$NAME" --cmd-done --timeout 15 >/dev/null || fail "warm-up never finished"

# --- 1. a lagging marker must not answer for the next send -------------------
# The lag is not left to chance. `pipe.py` is the recorder: stop it, run a
# command, and its `C`/`D` exist only inside the kernel pipe. Inject the next
# line now and its offset is drawn from a file that has not seen any of them —
# which is precisely the state a loaded box puts the tool in.
#
# Freeze every match: tmux runs the pipe command through `sh -c`, so the
# raw.log path appears in the wrapper's argv too, and stopping only the shell
# leaves the recorder writing (measured — the first version of this test did
# exactly that, produced no lag, and passed against broken code). That is also
# why the lag is *checked* below rather than assumed, and why a box that cannot
# stop the recorder says so instead of quietly scoring nothing.
PIDS="$(pgrep -f "$DIR/raw.log" | tr '\n' ' ')"
[ -n "${PIDS// /}" ] || fail "no recorder process found for $DIR/raw.log"
frozen=""
for p in $PIDS; do kill -STOP "$p" 2>/dev/null || true; done
for p in $PIDS; do
  case "$(ps -o stat= -p "$p" 2>/dev/null | cut -c1)" in
    T) frozen=1 ;;
  esac
done
if [ -z "$frozen" ]; then
  for p in $PIDS; do kill -CONT "$p" 2>/dev/null || true; done
  echo "E2E-31 NOTE: this box swallows SIGSTOP (a stopped process never reports"
  echo "  state T under proot/sandbox), so a real recorder lag cannot be produced"
  echo "  here and the borrowed-marker section is skipped. That rule is pinned by"
  echo "  tests/unit/test_cmd_done_scope.py cd1/cd10, each mutation-checked."
  SKIP1=1
  $TD send -n "$NAME" --type "sleep 12" --key Enter >/dev/null || fail "send (sleep)"
else
  SKIP1=""
  $TD send -n "$NAME" --type "true" --key Enter >/dev/null || fail "send (planted command)"
  $TD send -n "$NAME" --type "sleep 12" --key Enter >/dev/null || fail "send (sleep)"
  for p in $PIDS; do kill -CONT "$p" 2>/dev/null || true; done

  set +e
  EARLY="$($TD wait -n "$NAME" --done --timeout 3 2>&1)"
  RC=$?
  set -e
  if [ "$RC" = 0 ]; then
    echo "---- --done answered inside a sleep 12: $EARLY"
    fail "a marker left in flight by the previous command was taken as ours"
  fi
  printf '%s\n' "$EARLY" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read().strip().splitlines()[-1])
code = d['error']['code']
assert code == 'timeout', f'expected a plain timeout, got {code}: {d}'
" || fail "the early answer was not a structured timeout (see above)"

  # Prove the plant happened. A `D` inside the window, ordered *before* our echo,
  # is the borrowed pair the rule must refuse; without one this section would
  # silently stop exercising anything.
  python3 -c "
from termuse import records, waiting
off = records.read_state('$NAME')['last_send_offset']
echo = waiting.echo_barrier('$NAME', off, 'sleep 12')
assert echo is not None, 'our own echo never reached the recording'
borrowed = [pos for _t, code, data, pos in records.events_since('$NAME', off)
            if code == 'o' and isinstance(data, str)
            and '\\x1b]133;D' in data and pos < echo]
assert borrowed, ('the recorder did not lag: nothing was in flight when send drew '
                  'its window, so this run proved nothing about attribution')
print('planted lag: %d borrowed done-marker(s) inside the window before the echo'
      % len(borrowed))
" || fail "the SIGSTOP plant did not produce a lagged marker (see above)"
fi

# The send records its own drain answer either way: that is the fact the
# keys-only fallback leans on, so it has to be written, not guessed later.
python3 -c "
import json
s = json.load(open('$DIR/state.json'))
assert isinstance(s.get('last_send_settled'), bool), s
" || fail "state.json must record whether the recorder had drained"

# And the binding the fix uses exists in a *real* recording, not just a fixture:
# the echo of the line we sent is locatable, ordered ahead of the command's own
# `C`. This half of the rule runs on every box, frozen recorder or not.
python3 -c "
from termuse import records, waiting
st = records.read_state('$NAME')
off = st['last_send_offset']
sent = records.last_sent_text('$NAME', off)
assert sent, f'no completed injection after the offset: {st}'
echo = waiting.echo_barrier('$NAME', off, sent)
assert echo is not None, f'echo of {sent!r} not findable in the recording'
" || fail "the echo binding does not exist on a real recording (see above)"

# --- 2. our own completion is still a fact ----------------------------------
set +e
LATE="$($TD wait -n "$NAME" --done --timeout 20 2>&1)"
RC=$?
set -e
[ "$RC" = 0 ] || { echo "---- $LATE"; fail "--done never answered for a command that had finished"; }
printf '%s\n' "$LATE" | python3 -c "
import json, sys
v = json.loads(sys.stdin.read().strip().splitlines()[-1])
assert v['verdict'] == 'met' and v['confidence'] == 'fact', v
assert v.get('exit_code') == 0, f'expected exit 0, got {v}'
# which channel answered is stated, and it is one of the two real ones
assert v.get('via') in ('osc133', 'tmux-command-status'), v
" || fail "the eventual answer was not a fact about our own command (see above)"

# --- 3. an expect-code mismatch on the *right* command still fails fast ------
$TD send -n "$NAME" --type "false" --key Enter >/dev/null \
  || fail "send (failing command)"
set +e
MISMATCH="$($TD wait -n "$NAME" --done --expect-code 0 --timeout 20 2>&1)"
RC=$?
set -e
[ "$RC" != 0 ] || { echo "---- $MISMATCH"; fail "--expect-code 0 accepted a command that failed"; }
printf '%s\n' "$MISMATCH" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read().strip().splitlines()[-1])
assert d['error']['code'] == 'expect-code-mismatch', d
" || fail "the mismatch did not surface as expect-code-mismatch (see above)"

echo "E2E-31 ok"
