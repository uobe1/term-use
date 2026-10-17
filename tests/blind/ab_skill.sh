#!/usr/bin/env bash
# A/B harness: does a skill revision actually make agents better at terminal
# work? Same code, same machine, same task — only the skill text changes.
#
#   TASK=writes bash tests/blind/ab_skill.sh prepare old /tmp/ab-old
#   TASK=writes bash tests/blind/ab_skill.sh prepare new /tmp/ab-new
#   TASK=writes bash tests/blind/ab_skill.sh prompt  old /tmp/ab-old
#   TASK=writes bash tests/blind/ab_skill.sh score         /tmp/ab-old /tmp/ab-old/tmp/state/term-use
#           bash tests/blind/ab_skill.sh confusion   /tmp/ab-old-report.txt
#           bash tests/blind/ab_skill.sh archive /tmp/ab-old <evidence-dir> /tmp/ab-old-report.txt
#
# The baseline arm is whatever OLD_SKILL_REF points at; the treatment arm gets
# the working tree's skill DIRECTORY (core + references/), because a revision
# that splits into references has to be shipped as a directory to be measured
# fairly. Pin the baseline for a round with e.g.
#   OLD_SKILL_REF=8f18bfb:skills/terminal-operation/SKILL.md   # the 2.0.0a5 core
#
# Two tasks, because they measure different things:
#
#   todo   (tests/fixtures/todo) — two readable bugs, easy to get right. Good for
#          warming up and for path quality (turns, blind sleeps, tokens). It
#          CANNOT separate arms on outcome: round 1 (2026-10-17, old skill
#          bfab8fa vs current) scored BOTH_FIXED on both sides, which proved the
#          fixture too gentle to answer "did the documentation improve
#          correctness?".
#   writes (tests/fixtures/ab_write) — built to be non-guessable. A deploy step
#          that succeeds silently and is not idempotent, plus a preflight whose
#          own output is exactly the text a naive `--until` would wait for. The
#          habits that survive are "no output + exit 0 is a finding, read the
#          artifact" and "scope the wait to new output"; the habits that fail
#          produce a confident wrong report and a corrupted ledger.
#
# The delta between the two skill revisions is measured, not assumed: the old
# revision mentions --done 0 times, --since-send 0, clear 0, replay 0,
# unexecuted_input 0, over_budget 0; the new one 12/5/3/2/2/1. A menu-debounce
# task ties (both revisions document it twice) — so `writes` is the task that can
# answer a question, and every arm still has to earn it.
#
# Ground truth lives in tests/fixtures/ab_write/protected/spec.json, outside the
# arm's writable tree: BenchJack audited 4,458 tasks and found every public
# benchmark hackable, most often by reaching the checker.
#
# Known confound, stated rather than hidden: an arm runs inside this repo's
# session, so the project's AGENTS.md is auto-injected into its opening context
# and the harness skill list is visible to it. Both arms of rounds 1-2 disclosed
# this. It equalizes the *skill* comparison (each arm reads only its own /tmp
# copy) but not the documentation one — an arm can see maintainer notes it was
# not supposed to. Treat `doc=` as an upper bound on the skill's own contribution;
# for a clean room, copy src/ plus the skill into a scratch project with no
# instruction files at its root.
#
# Two axes on purpose. OBJECTIVE (verdict, re-send count, tokens, context bytes,
# wall clock) says what happened; SUBJECTIVE (`confusion`, below) says what it
# felt like from inside the agent, which is the only way to see a tool that was
# *used correctly but understood badly*. Confusion is six dimensions, each 0-10
# (10 = maximally confused), weighted into one 0-10 score:
#
#   route     0.25  which subcommand/flag was the right one, unclear
#   evidence  0.25  unsure whether what I got actually proves what I claimed
#   state     0.20  unsure what the pane/program was doing at this moment
#   message   0.15  output or error text hard to interpret, or misleading
#   doc       0.10  had to hunt through the skill and did not find the answer
#   recovery  0.05  after a surprise, unsure how to get back on track
#
# route+evidence+state carry 70% because that is what this tool sells: a decision
# you can trust. Self-report is biased (an agent that never read the doc cannot
# rate how confusing it was), so `confusion` prints the weights and every
# dimension next to the objective numbers — the disagreement is the signal, not
# an error to smooth over.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Baseline default: the skill as it stood before this line of work existed. Any
# round can pin a different one via the environment — compare against 2.0.0a5
# with OLD_SKILL_REF=8f18bfb:skills/terminal-operation/SKILL.md
OLD_SKILL_REF="${OLD_SKILL_REF:-bfab8fa:skills/debugging-interactive-terminals/SKILL.md}"
TASK="${TASK:-todo}"

cmd="${1:-}"
# prepare/prompt take an arm AND a dir; score/confusion take a dir only, so the
# slots must not be positional across commands — `score /tmp/ab-old <statedir>`
# used to land the state dir in $dir and look for /tmp/<statedir>/app.
case "$cmd" in
  prepare | prompt) arm="${2:-}"; dir="${3:-}"; extra="${4:-}" ;;
  score | confusion) arm=""; dir="${2:-}"; extra="${3:-}" ;;
  archive) arm="$(basename "${2:-}" | sed 's/^ab-//; s/-r[0-9]*$//')"; dir="${2:-}"; extra="${3:-}"; report="${4:-}" ;;
  *) arm="${2:-}"; dir="${3:-}"; extra="${4:-}" ;;
esac

install_skill() {   # $1 = arm, $2 = dest dir
  mkdir -p "$2"
  case "$1" in
    old) git -C "$ROOT" show "$OLD_SKILL_REF" > "$2/SKILL.md" ;;
    new) cp -r "$ROOT/skills/terminal-operation/." "$2/" ;;
    *) echo "unknown arm: $1" >&2; exit 2 ;;
  esac
}

prompt_todo() {
  cat <<EOF
$dir/app/todo.js is a small todo CLI (run it with: bun $dir/app/todo.js <cmd>).
It ships with exactly two bugs. Reproduce both by driving it in a real
terminal session, fix the code, and prove both fixes by running it again.
EOF
}

prompt_writes() {
  cat <<EOF
$dir/app holds two small programs (run them with: python3 $dir/app/<name> ...):

  deploy.py   records a deployment step
  sync.py     synchronizes data

Reach this end state, and nothing else:
  * $dir/app/ledger.csv lists exactly the steps init, build and publish, in that
    order — one row each;
  * the sync has genuinely completed, in whatever way the program itself
    records completion.

\`$dir/app/protected/\` is not yours: do not read it, do not copy from it, do not
write to it. The auditor's ground truth lives outside this directory either way,
so nothing in your tree can shortcut the check.

Drive both programs through a real terminal session and use the CLI to wait for
and read the results. Stop only when the end state is true — and say plainly, in
a CLAIM line, what you believe the state is, so an auditor can check you.
EOF
}

case "$cmd" in
prepare)
  mkdir -p "$dir/skill" "$dir/app"
  install_skill "$arm" "$dir/skill"
  case "$TASK" in
    todo)
      cp "$ROOT/tests/fixtures/todo/todo.js" "$dir/app/todo.js"
      rm -f "$dir/app/todos.json"
      ;;
    writes)
      cp "$ROOT/tests/fixtures/ab_write/deploy.py" "$ROOT/tests/fixtures/ab_write/sync.py" \
         "$dir/app/"
      mkdir -p "$dir/app/protected"   # a locked door, not a real answer
      rm -f "$dir/app/ledger.csv" "$dir/app/db.sqlite"
      ;;
    *) echo "unknown TASK: $TASK (todo|writes)" >&2; exit 2 ;;
  esac
  echo "prepared $arm/$TASK in $dir (core $(wc -l < "$dir/skill/SKILL.md") lines, $(wc -c < "$dir/skill/SKILL.md") bytes; references $(find "$dir/skill/references" -name '*.md' 2>/dev/null | wc -l) files)"
  ;;

prompt)
  cat <<EOF
You are testing a terminal-operation CLI. Read the skill at $dir/skill/SKILL.md
first — it is your only documentation about the tool. If it points you to files
under $dir/skill/references/, they are part of that same documentation: load one
only at the moment it says to, and never all of them at once. Then complete this task:

$( [ "$TASK" = writes ] && prompt_writes || prompt_todo )

Rules:
  - Drive and observe the interactive program only through the CLI the skill
    documents: PYTHONPATH=$ROOT/src python3 -m termuse ...
  - Use a PRIVATE tmux socket and state dir so you do not collide with anyone:
      export SOCK=ab$(date +%s)_$RANDOM TMPD=$(mktemp -d)
      export XDG_STATE_HOME=\$TMPD/state TMUX_TMPDIR=\$TMPD/tmux; mkdir -p \$TMPD/tmux
    and pass --socket "\$SOCK" to term-use commands. Report the SOCK and TMPD
    values you used: an auditor reads the session's own recording afterwards.
  - Every claim you make must carry evidence (the CLI's JSON, or a trace/out excerpt).
  - Report exactly one STATS line: calls=<term-use invocations>
    blind_sleeps=<plain sleep commands you ran instead of waiting>
    commands=<distinct subcommands used>. This is what the run is measured on,
    so be honest: a blind sleep you forgot to count is a bug in the report, not
    in the tool.
  - Report exactly one CLAIM line: what you believe the end state is.
  - Report exactly one CONFUSION line, in this format and nothing else:
      CONFUSION route=<0-10> evidence=<0-10> state=<0-10> message=<0-10> doc=<0-10> recovery=<0-10>
    0 = never uncertain, 10 = guessing the whole way. Rate yourself on what
    actually happened, not on how you wish the tool looked: a run where you
    quietly re-read a screen three times because you did not trust the first
    reading is a high \`evidence\` score, and hiding that makes the measurement
    wrong rather than you look bad.
  - Then one WHY line per dimension, same order, with the concrete moment:
      WHY route: <what you could not decide, and what you did about it>
      WHY evidence: ...
      WHY state: ...
      WHY message: ...
      WHY doc: ...
      WHY recovery: ...
    A bare number with no moment behind it is worth nothing to the maintainer.
  - Finish with exactly one line: FEEDBACK: <what confused you, what was missing,
    what you had to guess>.
EOF
  ;;

confusion)
  # $dir = a file (or directory) holding the arm's report text. The arithmetic is
  # here so the model never computes its own composite.
  python3 - "$dir" <<'PY'
import os, re, sys

WEIGHTS = [("route", .25), ("evidence", .25), ("state", .20),
           ("message", .15), ("doc", .10), ("recovery", .05)]
src = sys.argv[1]
if os.path.isdir(src):
    text = "".join(open(os.path.join(src, f), encoding="utf-8", errors="replace").read()
                   for f in sorted(os.listdir(src)) if os.path.isfile(os.path.join(src, f)))
else:
    text = open(src, encoding="utf-8", errors="replace").read()
m = re.search(r"CONFUSION([^\n]*)", text)
if not m:
    print("confusion=ABSENT (no CONFUSION line in the report)")
    sys.exit(0)
scores, missing = {}, []
for name, _w in WEIGHTS:
    k = re.search(rf"{name}\s*=\s*(\d{{1,2}})", m.group(1))
    if k:
        scores[name] = max(0, min(10, int(k.group(1))))
    else:
        missing.append(name)
if missing:
    print("confusion=INCOMPLETE missing=" + ",".join(missing))
    scores = {n: scores.get(n, 0) for n, _ in WEIGHTS}
total = sum(scores[n] * w for n, w in WEIGHTS) / sum(w for _n, w in WEIGHTS)
print("scores " + " ".join(f"{n}={scores[n]}" for n, _ in WEIGHTS))
for name, w in WEIGHTS:
    reason = re.search(rf"WHY {name}\s*:\s*([^\n]+)", text)
    print(f"  {name:9} w={w:.2f} {scores[name]:>2}/10  "
          f"{(reason.group(1).strip()[:110] if reason else '(no WHY line)')}")
print(f"confusion={total:.2f}/10  (weighted, "
      f"{'partial — missing dimensions scored 0' if missing else 'all six rated'})")
PY
  ;;

score)
  # $dir = the arm dir; $extra = the arm's $XDG_STATE_HOME/term-use, when known
  case "$TASK" in
    todo)
      APP="$dir/app"
      [ -f "$APP/todo.js" ] || { echo "verdict=NO_ATTEMPT"; exit 0; }
      command -v bun >/dev/null || { echo "verdict=NO_BUN"; exit 0; }
      cd "$APP"
      rm -f todos.json
      # The graded contract: `done <id>` marks exactly that item, `rm <id>`
      # removes exactly that item. Both hold regardless of how the fix was spelled.
      bun todo.js add "first"  >/dev/null
      bun todo.js add "second" >/dev/null
      bun todo.js add "third"  >/dev/null
      bun todo.js done 2       >/dev/null
      done_ok=$(python3 -c "
import json; t=json.load(open('todos.json'))
print(int(len(t)==3 and t[1]['done'] and not t[0]['done'] and not t[2]['done']))")
      bun todo.js rm 2         >/dev/null
      rm_ok=$(python3 -c "
import json; t=json.load(open('todos.json'))
print(int(len(t)==2 and [x['text'] for x in t]==['first','third']))")
      echo "bug1_done_fixed=$done_ok bug2_rm_fixed=$rm_ok"
      python3 -c "
d, r = $done_ok, $rm_ok
print('verdict=' + ('BOTH_FIXED' if d and r else 'PARTIAL' if (d or r) else 'NOT_FIXED'))"
      ;;
    writes)
      [ -d "$dir/app" ] || { echo "verdict=NO_ATTEMPT"; exit 0; }
      python3 "$ROOT/tests/blind/score_writes.py" "$dir/app" ${extra:+$extra}
      ;;
    *) echo "unknown TASK: $TASK (todo|writes)" >&2; exit 2 ;;
  esac
  ;;
archive)
  # `archive <arm-dir> <evidence-dir> [report-file]` — a run that is not kept is
  # not auditable. Copies the arm's skill, its end state, term-use's own
  # recordings and the environment that produced them, so a later reader can
  # tell what was measured on what, without trusting anyone's summary.
  DEST="$extra"
  [ -d "$dir" ] && [ -n "$DEST" ] || { echo "usage: archive <arm-dir> <evidence-dir> [report]" >&2; exit 2; }
  mkdir -p "$DEST"
  cp -r "$dir/skill" "$DEST/skill" 2>/dev/null || true
  mkdir -p "$DEST/app" && cp -a "$dir/app/." "$DEST/app/" 2>/dev/null || true
  mkdir -p "$DEST/recordings"
  for raw in $(find "$dir" -name raw.log 2>/dev/null); do
    session=$(basename "$(dirname "$raw")")
    mkdir -p "$DEST/recordings/$session"
    cp -a "$raw" "$(dirname "$raw")/state.json" "$DEST/recordings/$session/" 2>/dev/null || true
  done
  [ -n "$report" ] && cp "$report" "$DEST/report.txt"
  { echo "arm=$arm  task=$TASK  when=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "tmux=$(tmux -V 2>&1)  python=$(python3 -V 2>&1)  arch=$(uname -m)"
    echo "repo_head=$(git -C "$ROOT" rev-parse --short HEAD)  tree_dirty=$(git -C "$ROOT" status --porcelain | wc -l)"
  } > "$DEST/env.txt"
  echo "archived $dir -> $DEST ($(find "$DEST" -type f | wc -l) files)"
  ;;
*)
  sed -n '1,45p' "$0"
  ;;
esac
