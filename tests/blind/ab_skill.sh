#!/usr/bin/env bash
# A/B harness: does a skill revision actually make agents better at terminal
# work? Same code, same machine, same task — only the skill text changes.
#
#   TASK=todo   bash tests/blind/ab_skill.sh prepare old /tmp/ab-old
#   TASK=writes bash tests/blind/ab_skill.sh prepare old /tmp/ab-old
#   TASK=writes bash tests/blind/ab_skill.sh prompt  old /tmp/ab-old
#   TASK=writes bash tests/blind/ab_skill.sh score         /tmp/ab-old /tmp/STATE/term-use
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
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OLD_SKILL_REF="bfab8fa:skills/debugging-interactive-terminals/SKILL.md"
NEW_SKILL="skills/terminal-operation/SKILL.md"
TASK="${TASK:-todo}"

cmd="${1:-}"; arm="${2:-}"; dir="${3:-}"; extra="${4:-}"

skill_src() {
  case "$1" in
    old) git -C "$ROOT" show "$OLD_SKILL_REF" ;;
    new) cat "$ROOT/$NEW_SKILL" ;;
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

`$dir/app/protected/` is not yours: do not read it, do not copy from it, do not
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
  skill_src "$arm" > "$dir/skill/SKILL.md"
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
  echo "prepared $arm/$TASK in $dir ($(wc -l < "$dir/skill/SKILL.md") skill lines)"
  ;;

prompt)
  cat <<EOF
You are testing a terminal-operation CLI. Read the skill at $dir/skill/SKILL.md
first — it is your only documentation about the tool. Then complete this task:

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
  - Finish with exactly one line: FEEDBACK: <what confused you, what was missing,
    what you had to guess>.
EOF
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
*)
  sed -n '1,40p' "$0"
  ;;
esac
