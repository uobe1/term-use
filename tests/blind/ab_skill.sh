#!/usr/bin/env bash
# A/B harness: does a skill revision actually make agents better at terminal
# work? Same code, same machine, same task — only the skill text changes.
#
#   bash tests/blind/ab_skill.sh prepare old  /tmp/arm-old   # materialize an arm
#   bash tests/blind/ab_skill.sh prompt  old  /tmp/arm-old   # print the tester prompt
#   bash tests/blind/ab_skill.sh score         /tmp/arm-old  # objective verdict
#
# The scored task ships with exactly two bugs (tests/fixtures/todo/todo.js):
#   1. `done <id>` compares a number to a string, so it silently does nothing
#   2. `rm <id>` calls splice(idx) with no delete count, wiping the tail
# Both are checkable from the file state afterwards, which is the point: we
# grade outcomes, not prose.
#
# Round 1 (2026-10-17, old skill bfab8fa vs current, same code): both arms
# scored BOTH_FIXED, so this fixture cannot separate the arms on outcome — use
# it for path quality (turns, wall clock, blind sleeps, tool-side context) and
# reach for a harder target (a TUI with a debounce, or a data-corrupting menu
# flow) when the question is "did it get more things right".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OLD_SKILL_REF="bfab8fa:skills/debugging-interactive-terminals/SKILL.md"
NEW_SKILL="skills/terminal-operation/SKILL.md"

cmd="${1:-}"; arm="${2:-}"; dir="${3:-$2}"   # score takes only a dir: fall back to $2

skill_src() {
  case "$1" in
    old) git -C "$ROOT" show "$OLD_SKILL_REF" ;;
    new) cat "$ROOT/$NEW_SKILL" ;;
    *) echo "unknown arm: $1" >&2; exit 2 ;;
  esac
}

case "$cmd" in
prepare)
  mkdir -p "$dir/skill" "$dir/app"
  skill_src "$arm" > "$dir/skill/SKILL.md"
  cp "$ROOT/tests/fixtures/todo/todo.js" "$dir/app/todo.js"
  rm -f "$dir/app/todos.json"
  echo "prepared $arm in $dir ($(wc -l < "$dir/skill/SKILL.md") skill lines)"
  ;;

prompt)
  cat <<EOF
You are testing a terminal-operation CLI. Read the skill at $dir/skill/SKILL.md
first — it is your only documentation about the tool. Then complete this task:

  $dir/app/todo.js is a small todo CLI (run it with: bun $dir/app/todo.js <cmd>).
  It ships with exactly two bugs. Reproduce both by driving it in a real
  terminal session, fix the code, and prove both fixes by running it again.

Rules:
  - Drive and observe the interactive program only through the CLI the skill
    documents: PYTHONPATH=$ROOT/src python3 -m termuse ...
  - Use a PRIVATE tmux socket and state dir so you do not collide with anyone:
      export SOCK=ab$(date +%s)_$RANDOM TMPD=$(mktemp -d)
      export XDG_STATE_HOME=\$TMPD/state TMUX_TMPDIR=\$TMPD/tmux; mkdir -p \$TMPD/tmux
    and pass --socket "\$SOCK" to term-use commands.
  - Every claim you make must carry evidence (the CLI's JSON, or a trace/out excerpt).
  - Report exactly one STATS line first: calls=<term-use invocations>
    blind_sleeps=<plain sleep commands you ran instead of waiting>
    commands=<distinct subcommands used>. This is what the run is measured on,
    so be honest: a blind sleep you forgot to count is a bug in the report, not
    in the tool.
  - Finish with exactly one line: FEEDBACK: <what confused you, what was missing,
    what you had to guess>.
EOF
  ;;

score)
  APP="$dir/app"
  [ -f "$APP/todo.js" ] || { echo "verdict=NO_ATTEMPT"; exit 0; }
  command -v bun >/dev/null || { echo "verdict=NO_BUN"; exit 0; }
  cd "$APP"
  rm -f todos.json
  bun todo.js add "first" >/dev/null; bun todo.js add "second" >/dev/null
  bun todo.js add "third" >/dev/null
  bun todo.js done 2 >/dev/null
  done_ok=$(python3 -c "
import json; t=json.load(open('todos.json'))
print(int(len(t)==3 and t[1]['done'] and not t[0]['done'] and not t[2]['done']))")
  bun todo.js rm 2 >/dev/null
  rm_ok=$(python3 -c "
import json; t=json.load(open('todos.json'))
print(int(len(t)==2 and [x['text'] for x in t]==['first','third']))")
  echo "bug1_done_fixed=$done_ok bug2_rm_fixed=$rm_ok"
  python3 -c "
import sys
d, r = $done_ok, $rm_ok
print('verdict=' + ('BOTH_FIXED' if d and r else 'PARTIAL' if (d or r) else 'NOT_FIXED'))"
  ;;
*)
  sed -n '1,16p' "$0"
  ;;
esac
