#!/usr/bin/env bash
# E2E-28: verify the measuring instrument.
#
# The `writes` A/B fixture decides whether a documentation revision helped, so it
# is tested like the product: the happy path must be reachable through the CLI
# alone, both traps must actually fire, and the scorer must tell those two
# stories apart from disk state. An unvalidated instrument is worse than none.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td28sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux" "$TMP/app"
export TMUX_TMPDIR="$TMP/tmux"
# The trap's window has to outlast the test's own CLI calls: each invocation costs
# ~1-3s of process startup on this box, and six seconds of sync is not six seconds
# of wall clock when the suite is loaded.
export SYNC_RUN_SECONDS=25

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-28 FAIL: $*"; exit 1; }
cp "$ROOT/tests/fixtures/ab_write/deploy.py" "$ROOT/tests/fixtures/ab_write/sync.py" "$TMP/app/"

# step <session> <command>: send it, wait for the command itself, and nothing else.
step() {
  local name="$1"; shift
  $TD send -n "$name" --socket "$SOCK" --type "cd $TMP/app && $*" --key Enter >/dev/null
  $TD wait -n "$name" --socket "$SOCK" --done --timeout 60 >/dev/null \
    || fail "$* never finished"
}

$TD start -n td28 --cmd bash --socket "$SOCK" --width 100 --height 30 >/dev/null \
  || fail "start failed"
$TD wait -n td28 --socket "$SOCK" --shell-ready --timeout 15 >/dev/null \
  || fail "shell not ready"

# --- 1. silent, non-idempotent, and still provable: the artifact is the truth --
step td28 "python3 deploy.py init"
step td28 "python3 deploy.py build"
step td28 "python3 deploy.py publish"
python3 -c "
import csv
rows = list(csv.reader(open('$TMP/app/ledger.csv')))
assert rows[0] == ['seq','step','status'], rows
assert [r[1] for r in rows[1:]] == ['init','build','publish'], rows
" || fail "three steps must leave exactly three rows (see above)"

# `deploy.py` prints nothing at all, so "did it run" is only answerable by reading
# the file — which is what just happened, twice over: through the CLI as well.
step td28 "cat ledger.csv"
$TD out -n td28 --socket "$SOCK" | grep -q publish \
  || fail "out must show the ledger contents"

# --- 2. the stale anchor: the preflight's own line satisfies a naive wait ------
step td28 "python3 sync.py check"        # prints SYNC: COMPLETE, writes nothing
$TD send -n td28 --socket "$SOCK" --type "python3 sync.py run" --key Enter >/dev/null
sleep 1
if $TD wait -n td28 --socket "$SOCK" --until 'SYNC: COMPLETE' --timeout 2 \
     >/dev/null 2>&1; then
  : # expected, and this IS the trap: it is answering about the preflight
else
  fail "the stale anchor should satisfy a plain --until; if it does not, the fixture cannot discriminate"
fi
[ ! -e "$TMP/app/db.sqlite" ] \
  || fail "the db appeared too fast: the trap needs a window where the anchor is true and the sync is not"
$TD wait -n td28 --socket "$SOCK" --done --timeout 60 >/dev/null \
  || fail "sync.py run never finished"

# the same question scoped to new output must refuse to lie: `run` prints nothing,
# so there is no new `SYNC: COMPLETE` to find
if $TD wait -n td28 --socket "$SOCK" --until 'SYNC: COMPLETE' --since-send --timeout 2 \
     >/dev/null 2>&1; then
  fail "--since-send matched output from before this send — scoping did not work"
fi
python3 -c "
import sqlite3
con = sqlite3.connect('$TMP/app/db.sqlite')
assert con.execute('PRAGMA user_version').fetchone()[0] == 7
assert [r[0] for r in con.execute('SELECT name FROM records ORDER BY name')] == ['alpha','beta','gamma']
" || fail "the real sync must leave the database it promises"

# --- 3. the scorer: success reads as success, a re-send reads as breakage -----
$TD stop -n td28 >/dev/null
verdict=$(python3 "$ROOT/tests/blind/score_writes.py" \
            "$TMP/app" "$XDG_STATE_HOME/term-use/td28")
echo "$verdict" | grep -q "verdict=BOTH_OK" || fail "correct run must score BOTH_OK: $verdict"
echo "$verdict" | grep -q "resends=0" || fail "correct run must show no re-sends: $verdict"
echo "$verdict" | grep -q "check=1" || fail "the preflight must be counted once: $verdict"

rm -f "$TMP/app/ledger.csv" "$TMP/app/db.sqlite"
$TD start -n td28b --cmd bash --socket "$SOCK" --width 100 --height 30 >/dev/null
step td28b "python3 deploy.py init"
step td28b "python3 deploy.py build"
step td28b "python3 deploy.py publish"
step td28b "python3 deploy.py publish"     # the classic mistake: nothing printed, send again
step td28b "python3 sync.py run"
$TD stop -n td28b >/dev/null
verdict2=$(python3 "$ROOT/tests/blind/score_writes.py" \
             "$TMP/app" "$XDG_STATE_HOME/term-use/td28b")
echo "$verdict2" | grep -q "verdict=LEDGER_BROKEN" \
  || fail "a re-sent step must be caught as a broken ledger: $verdict2"
echo "$verdict2" | grep -q "resends=1" \
  || fail "the re-send must be countable from the session's own recording: $verdict2"

echo "E2E-28 PASS"
