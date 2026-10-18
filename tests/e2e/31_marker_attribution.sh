#!/usr/bin/env bash
# E2E-31: a completion marker must be attributable to the send that caused it.
#
# `send` draws its window at the recording's current size, and the recorder lags
# the pane. Inject while the pane is still delivering bytes and the *previous*
# command's `C` and `D` both land inside the new window — a positional test then
# returns met / fact / exit 0 for a command that has not started (a loaded box
# hit this two seconds into a `sleep 12`).
#
# What this pins, and where it cannot:
#   * always: the send-side rule (a line behind an outstanding marker is not
#     narrated, so there is no marker to borrow; --done refuses it), the echo
#     binding the wait uses for a marker the shell itself emitted, the per-send
#     drain answer in state.json, a met `fact` for a fresh command, and
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
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"
export XDG_STATE_HOME="$TMP/state"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-31 FAIL: $*"; exit 1; }

cat > "$TMP/clean.bashrc" <<'RC'
PS1='\[\e]133;B\a\]tu-clean$ '
PS0='\[\e]133;C\a\]'
PROMPT_COMMAND='__tu_ec=$?; printf "\e]133;D;%s\a\e]133;A\a" "$__tu_ec"'
RC

$TD start -n "$NAME" --cmd "bash --rcfile $TMP/clean.bashrc -i" \
  --socket "$SOCK" --width 100 --height 30 >/dev/null || fail "start failed"
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
# leaves the recorder writing (an earlier version of this test did
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
  $TD send -n "$NAME" --type "sleep 12" --key Enter >"$TMP/sleep-send.json" \
    || fail "send (sleep)"
  for p in $PIDS; do kill -CONT "$p" 2>/dev/null || true; done
  python3 -c "
import json
r = json.load(open('$TMP/sleep-send.json'))
assert r.get('marker') in ('paused', 'shell'), r
# paused means the recorder never proved the previous completion; shell
# means the native channel did and the wait barrier must still reject the
# previous command's marker.
if r.get('marker') == 'paused':
    assert '__tu_ec' not in r['sent']['text'], r
" || fail "the sleep send carried an unexpected marker"

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
e = json.loads(sys.stdin.read().strip().splitlines()[-1])['error']
assert e['code'] in ('shell-protocol-unavailable', 'timeout'), e
if e['code'] == 'shell-protocol-unavailable':
    assert 'no completion marker' in (e.get('message') or ''), e
    assert (e.get('evidence') or {}).get('marker_source') == 'paused', e
else:
    tops = (e.get('evidence') or {}).get('conditions') or []
    leaves = [c for top in tops for c in (top.get('conditions') or [top])]
    assert any(c.get('degraded') == 'unattributed-marker' for c in leaves), e
" || fail "the early answer was not a refusal or an unattributed timeout (see above)"
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

# --- 2. once the previous command is done, a fresh command is a fact --------
# The paused line has no marker of its own; the clean proof is the next send,
# made after the shell has returned to its prompt.
$TD wait -n "$NAME" --until 'tu-clean[$]' --since-send --timeout 25 >/dev/null \
  || fail "the shell never returned after the sleep"
$TD send -n "$NAME" --type "echo TD31_AFTER" --key Enter >/dev/null \
  || fail "send (after)"
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
assert 'TD31_AFTER' in (v.get('evidence') or {}).get('screen', ''), v
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

# --- 4. a clobbered shell must not lend a paused send the previous marker ----
# `send` answers `paused` when the previous tracked line has not reported back.
# That line carries no marker of its own, so a D arriving from the previous
# command must not be reported as its completion — the device shape that made
# --done claim fact/exit 0 two seconds into a sleep 12.
NAME2="${NAME}c"; DIR2="$XDG_STATE_HOME/term-use/$NAME2"
$TD start -n "$NAME2" \
  --cmd "bash --rcfile $ROOT/tests/fixtures/clobbering.bashrc -i" \
  --socket "$SOCK" --width 100 --height 30 >/dev/null \
  || fail "start (clobbered) failed"
$TD send -n "$NAME2" --type "true" --key Enter >/dev/null \
  || fail "clobbered warm-up send"
$TD wait -n "$NAME2" --cmd-done --timeout 15 >/dev/null \
  || fail "clobbered warm-up never finished"

PIDS2="$(pgrep -f "$DIR2/raw.log" | tr '\n' ' ')"
[ -n "${PIDS2// /}" ] || fail "no clobbered recorder process found"
for p in $PIDS2; do kill -STOP "$p" 2>/dev/null || true; done
frozen2=""
for p in $PIDS2; do
  case "$(ps -o stat= -p "$p" 2>/dev/null | cut -c1)" in
    T) frozen2=1 ;;
  esac
done
if [ -z "$frozen2" ]; then
  for p in $PIDS2; do kill -CONT "$p" 2>/dev/null || true; done
  echo "E2E-31 NOTE: SIGSTOP is not effective here; the clobbered-shell"
  echo "  borrowed-marker section is skipped. Its rule is pinned by the no-marker"
  echo "  unit tests and by the device run."
else
  $TD send -n "$NAME2" --type "true" --key Enter >"$TMP/clob-true.json" \
    || fail "clobbered planted true"
  $TD send -n "$NAME2" --type "sleep 12" --key Enter >"$TMP/clob-sleep.json" \
    || fail "clobbered sleep send"
  python3 -c "
import json
r = json.load(open('$TMP/clob-sleep.json'))
assert r.get('marker') == 'paused', f'the sleep send must not carry a marker: {r}'
assert '__tu_ec' not in r['sent']['text'], r
" || fail "clobbered sleep was narrated"
  for p in $PIDS2; do kill -CONT "$p" 2>/dev/null || true; done

  set +e
  EARLY2="$($TD wait -n "$NAME2" --cmd-done --timeout 3 2>&1)"
  RC2=$?
  set -e
  [ "$RC2" != 0 ] || { echo "---- $EARLY2"; fail "--cmd-done borrowed a previous marker for a paused send"; }
  printf '%s\n' "$EARLY2" | python3 -c "
import json, sys
e = json.loads(sys.stdin.read().strip().splitlines()[-1])['error']
assert e['code'] == 'shell-protocol-unavailable', e
assert 'no completion marker' in (e.get('message') or ''), e
assert (e.get('evidence') or {}).get('marker_source') == 'paused', e
assert '--until' in (e.get('hint') or ''), e
" || fail "paused-send refusal (see above)"
  $TD stop -n "$NAME2" --socket "$SOCK" >/dev/null
fi

echo "E2E-31 ok"
