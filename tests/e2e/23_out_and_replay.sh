#!/usr/bin/env bash
# E2E-23: reading output, and going back in time.
#   `out`    — the bytes the pane emitted since the last send, as text, inside
#              an explicit context budget (the single most common agent ask).
#   `replay` — the screen at any earlier moment, rebuilt by feeding the
#              recorded bytes back through tmux itself (no second emulator, so
#              no second opinion on what a sequence means).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td23sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true
            tmux -L tu-replay kill-server >/dev/null 2>&1 || true
            rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-23 FAIL: $*"; exit 1; }

$TD start -n td23 --cmd bash --socket "$SOCK" --width 80 --height 24 >/dev/null \
  || fail "start failed"
$TD wait -n td23 --shell-ready --timeout 10 >/dev/null || fail "shell not ready"

# --- out: what my last command printed -------------------------------------
$TD send -n td23 --type "echo OUT_MARKER_23" --key Enter >/dev/null
$TD wait -n td23 --done --timeout 10 >/dev/null || fail "command never finished"
$TD out -n td23 >"$TMP/out.txt" || fail "out failed"
grep -q 'OUT_MARKER_23' "$TMP/out.txt" || fail "out must contain the new output"
# scoped to the last send: an earlier marker must not appear
$TD send -n td23 --type "echo SECOND_23" --key Enter >/dev/null
$TD wait -n td23 --done --timeout 10 >/dev/null
$TD out -n td23 >"$TMP/out2.txt" || fail "out 2 failed"
grep -q 'SECOND_23' "$TMP/out2.txt" || fail "out missed the newest command"
grep -q 'OUT_MARKER_23' "$TMP/out2.txt" \
  && fail "out must be scoped to the last send, not the whole session"
$TD out -n td23 --all | grep -q 'OUT_MARKER_23' || fail "--all must reach back"

# --- out: JSON shape + budget enforcement ----------------------------------
$TD send -n td23 --type "seq -f 'row %g pad pad pad pad pad pad pad' 1 400" --key Enter >/dev/null
$TD wait -n td23 --done --timeout 30 >/dev/null || fail "seq never finished"
$TD out -n td23 --json --max-bytes 2000 >"$TMP/big.json" || fail "out --json failed"
python3 -c "
import json
r = json.load(open('$TMP/big.json'))
assert r['truncated'] is True, r
assert r['omitted_bytes'] > 0, r
assert 'row 1 ' in r['text'] and 'row 400' in r['text'], 'head and tail must both survive'
assert len(r['text'].encode()) < 4000, len(r['text'])
assert 'omitted' in r['text'], 'the elision must be marked, never silent'
assert r['context']['bytes'] <= 4000, r['context']
assert r['context']['full_bytes'] > 4000, r['context']
" || fail "out budget/truncation (see above)"

# --- replay: the screen at a moment in the past ----------------------------
cat > "$TMP/timeline.sh" <<'SH'
#!/bin/bash
printf 'FRAME_EARLY_ONLY\n'
sleep 2
printf 'FRAME_LATE_ADDED\n'
sleep 300
SH
chmod +x "$TMP/timeline.sh"
$TD start -n td23t --cmd "bash $TMP/timeline.sh" --socket "$SOCK" \
  --width 60 --height 12 >/dev/null || fail "timeline start failed"
sleep 1
$TD wait -n td23t --until 'FRAME_EARLY_ONLY' --timeout 5 >/dev/null \
  || fail "early frame never painted"

# Both frames must exist before we anchor on the recording's own clock:
# asserting on wall-clock guesses (replay at "1.0s") is racy — the program
# decides when it prints, and under load that can be later than 1s.
$TD wait -n td23t --until 'FRAME_LATE_ADDED' --timeout 15 >/dev/null \
  || fail "late frame never came"
read -r EARLY LATE < <($TD trace -n td23t --format json | python3 -c '
import json, sys
# Accumulate: raw.log stores pipe *chunks*, so a short line can arrive split
# across two events and must never be matched per-event.
acc, e, l = "", 0.0, 0.0
for line in sys.stdin:
    ev = json.loads(line)
    if isinstance(ev, list) and len(ev) > 2 and ev[1] == "o":
        acc += ev[2]
        if not e and "FRAME_EARLY_ONLY" in acc:
            e = ev[0]
        if not l and "FRAME_LATE_ADDED" in acc:
            l = ev[0]
assert 0 < e < l, (e, l)
print(e, l)')
MID=$(python3 -c "print(round(($EARLY + $LATE) / 2, 3))")

# --- replay: the screen at a moment in the past ----------------------------
$TD replay -n td23t --at "$MID" --format text >"$TMP/early.txt" \
  || fail "replay --at $MID failed"
grep -q 'FRAME_EARLY_ONLY' "$TMP/early.txt" || fail "early replay lost the early frame"
grep -q 'FRAME_LATE_ADDED' "$TMP/early.txt" \
  && fail "replay between the two prints must not show the later one"

$TD replay -n td23t --at "$(python3 -c "print($LATE + 0.3)")" --format text >"$TMP/late.txt" \
  || fail "replay after the second print failed"
grep -q 'FRAME_LATE_ADDED' "$TMP/late.txt" || fail "late replay lost the late frame"
grep -q 'FRAME_EARLY_ONLY' "$TMP/late.txt" || fail "late replay must still show the earlier frame"

# --- the strongest claim the engine makes: replaying everything reproduces
#     the live screen, because tmux itself is the emulator doing the replay ---
# Order matters, and so does the precondition. Reading the live screen *before*
# the rebuild spends seconds of replay while the program carries on painting —
# and if the program has exited, tmux writes an exit notice *over* the screen,
# which is what made this compare 2 replayed lines against 1 live line. So: the
# claim only holds for a live program, check that, rebuild, then read until two
# reads agree.
$TD replay -n td23t --at 999 >"$TMP/full.txt"
$TD screen -n td23t --meta | python3 -c '
import json, sys
m = json.load(sys.stdin)
assert m.get("pane_dead") is not True, f"the program exited; the exit notice owns the screen: {m}"
' || fail "replay-vs-live needs a live pane (see above)"
: >"$TMP/prev.txt"
for _ in 1 2 3 4 5 6 7 8; do
  $TD screen -n td23t >"$TMP/live.txt"
  if [ -s "$TMP/live.txt" ] && cmp -s "$TMP/live.txt" "$TMP/prev.txt"; then break; fi
  cp "$TMP/live.txt" "$TMP/prev.txt"
  sleep 0.4
done
[ -s "$TMP/live.txt" ] || fail "the live pane never painted anything to compare against"
python3 - "$TMP/live.txt" "$TMP/full.txt" <<'PY' || fail "full replay must rebuild the live screen exactly"
import sys
def norm(p):
    lines = [ln.rstrip() for ln in open(p, encoding="utf-8").read().split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return lines
a, b = norm(sys.argv[1]), norm(sys.argv[2])
assert a == b, "\n".join([f"live={a[i]!r} replay={b[i]!r}"
                          for i in range(min(len(a), len(b))) if a[i] != b[i]]
                         ) or f"lengths differ: {len(a)} vs {len(b)}"
PY

# offsets and the last-send anchor are addressable too
$TD replay -n td23t --at last-send --format json | python3 -c '
import json, sys
r = json.load(sys.stdin)
assert r["ok"] and r["at"] and r["grid"], r
# A frame is a screen *and* a position: the replayed pane must report where it
# left the cursor, inside the geometry the replay actually ended on.
c, (w, h) = r["cursor"], r["grid"]
assert isinstance(c, list) and len(c) == 2, f"no cursor in the replayed frame: {r}"
assert 0 <= c[0] < w and 0 <= c[1] < h, f"cursor {c} outside grid {w}x{h}"
' || fail "replay --at last-send --format json"

# --- replay must not leave a mess or fake a managed session -----------------
[ ! -d "$XDG_STATE_HOME/term-use/tu-replay" ] \
  || fail "the scratch replay server must not create record dirs"
$TD replay -n td23t --at 2.0 --format png -o "$TMP/f.png" >/dev/null \
  || fail "replay --format png failed"
python3 -c "
import json, os
assert os.path.getsize('$TMP/f.png') > 100
" || fail "replayed frame png"
# `--no-cursor` is the same switch `screenshot` has, with the same effect
$TD replay -n td23t --at 2.0 --format json --no-cursor | python3 -c '
import json, sys
r = json.load(sys.stdin)
assert r["cursor"] is None, "--no-cursor must omit it: " + repr(r["cursor"])
' || fail "replay --no-cursor"

$TD stop -n td23 >/dev/null; $TD stop -n td23t >/dev/null
echo "E2E-23 PASS"
