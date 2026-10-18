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
# fairly. Pin the baseline for a round with a file or a directory, e.g.
#   OLD_SKILL_REF=8f18bfb:skills/terminal-operation/SKILL.md   # an earlier core
#   OLD_SKILL_REF=e093271:skills/terminal-operation             # core + refs
#
# Three tasks, because they measure different things:
#
#   todo   (tests/fixtures/todo) — two readable bugs, easy to get right. Good for
#          warming up and for path quality (turns, blind sleeps, tokens). It
#          CANNOT separate arms on outcome: an earlier round (old skill
#          bfab8fa vs current) scored BOTH_FIXED on both sides, which proved the
#          fixture too gentle to answer "did the documentation improve
#          correctness?".
#   writes (tests/fixtures/ab_write) — built to be non-guessable. A deploy step
#          that succeeds silently and is not idempotent, plus a preflight whose
#          own output is exactly the text a naive `--until` would wait for. The
#          habits that survive are "no output + exit 0 is a finding, read the
#          artifact" and "scope the wait to new output"; the habits that fail
#          produce a confident wrong report and a corrupted ledger.
#   general (tests/fixtures/ab_general) — the fact channel refuses in three
#          shapes: a program holding the tty, a background line, and a shell
#          whose name the tool cannot narrate. The habits that survive are the
#          general fallbacks (own marker, --until --since-send, screen/out);
#          the habits that fail wait for a marker that can never arrive.
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
# round can pin a different one via the environment — compare against an earlier
# revision with OLD_SKILL_REF=8f18bfb:skills/terminal-operation/SKILL.md
OLD_SKILL_REF="${OLD_SKILL_REF:-bfab8fa:skills/debugging-interactive-terminals/SKILL.md}"
TASK="${TASK:-todo}"

cmd="${1:-}"
# prepare/prompt take an arm AND a dir; score/confusion take a dir only, so the
# slots must not be positional across commands — `score /tmp/ab-old <statedir>`
# used to land the state dir in $dir and look for /tmp/<statedir>/app.
case "$cmd" in
  prepare | prompt) arm="${2:-}"; dir="${3:-}"; extra="${4:-}" ;;
  score | confusion) arm=""; dir="${2:-}"; extra="${3:-}" ;;
  archive) arm="$(basename "${2:-}" | sed 's/^ab[0-9]*-//; s/-r[0-9]*$//')"; dir="${2:-}"; extra="${3:-}"; report="${4:-}" ;;
  *) arm="${2:-}"; dir="${3:-}"; extra="${4:-}" ;;
esac

install_skill() {   # $1 = arm, $2 = dest dir
  mkdir -p "$2"
  case "$1" in
    old)
      # `git show <ref> > SKILL.md` truncates the destination *before* git runs,
      # so a bad ref leaves a 0-byte baseline that every later stage measures as
      # "the old skill". Resolve the ref and the path first, write aside, move
      # into place only once the file is non-empty.
      ref="${OLD_SKILL_REF%%:*}"; path="${OLD_SKILL_REF#*:}"
      git -C "$ROOT" rev-parse --verify "${ref}^{commit}" >/dev/null || {
        echo "OLD_SKILL_REF: no such commit '$ref' (from $OLD_SKILL_REF)" >&2; exit 2; }
      if git -C "$ROOT" cat-file -e "${ref}:${path}/SKILL.md" 2>/dev/null; then
        # A directory baseline: the fair arm for a split skill is core + its
        # references, not the core alone against the candidate's directory.
        tmpdir="$(mktemp -d)"
        depth=$(printf '%s' "$path" | awk -F/ '{print NF}')
        git -C "$ROOT" archive --format=tar "$ref" "$path" \
          | tar -x -C "$tmpdir" --strip-components="$depth" || {
            rm -rf "$tmpdir"; echo "OLD_SKILL_REF: archive $OLD_SKILL_REF failed" >&2; exit 2; }
        [ -s "$tmpdir/SKILL.md" ] || {
          rm -rf "$tmpdir"
          echo "OLD_SKILL_REF: $OLD_SKILL_REF is empty — refusing to score against a 0-byte baseline" >&2; exit 2; }
        rm -rf "$2"/* "$2"/.[!.]* 2>/dev/null || true
        cp -a "$tmpdir/." "$2/"
        rm -rf "$tmpdir"
      elif git -C "$ROOT" cat-file -e "${ref}:${path}" 2>/dev/null; then
        git -C "$ROOT" show "$OLD_SKILL_REF" > "$2/.SKILL.md.tmp" || {
          rm -f "$2/.SKILL.md.tmp"; echo "OLD_SKILL_REF: git show $OLD_SKILL_REF failed" >&2; exit 2; }
        [ -s "$2/.SKILL.md.tmp" ] || {
          rm -f "$2/.SKILL.md.tmp"
          echo "OLD_SKILL_REF: $OLD_SKILL_REF is empty — refusing to score against a 0-byte baseline" >&2; exit 2; }
        mv "$2/.SKILL.md.tmp" "$2/SKILL.md"
      else
        echo "OLD_SKILL_REF: '$path' is not in '$ref' — that path only exists on the" >&2
        echo "line that has it; pick a ref whose tree carries the file (see:" >&2
        echo "  git -C $ROOT ls-tree --name-only '$ref' skills/)" >&2; exit 2
      fi ;;
    new) cp -r "$ROOT/skills/terminal-operation/." "$2/" ;;
    *) echo "unknown arm: $1" >&2; exit 2 ;;
  esac
}

require_arm_dir() {   # $1 = the directory `prepare` made for one arm
  [ -n "$1" ] || { echo "score/archive: give the arm directory (prepare made it)" >&2; exit 2; }
  [ -d "$1" ] || { echo "score/archive: '$1' is not a directory" >&2; exit 2; }
  [ -d "$1/skill" ] || [ -d "$1/app" ] || {
    echo "score/archive: '$1' holds neither skill/ nor app/, so it is not an arm dir." >&2
    echo "A wrong path here does not fail: score falls through to verdict=NO_ATTEMPT and" >&2
    echo "archive copies nothing, so an empty or misnamed arm reads exactly like a real" >&2
    echo "run that did nothing. Check the path with: ls '$1'" >&2
    exit 2; }
}

require_report() {   # $1 = file (or directory) holding one arm's report text
  [ -n "$1" ] || { echo "confusion: give the report file, or a directory of them" >&2; exit 2; }
  [ -e "$1" ] || { echo "confusion: $1 does not exist" >&2; exit 2; }
  if [ -d "$1" ]; then
    [ -n "$(find "$1" -maxdepth 1 -type f -size +1c -print -quit)" ] || {
      echo "confusion: $1 holds no non-empty file" >&2; exit 2; }
  elif [ ! -s "$1" ]; then
    echo "confusion: $1 is empty" >&2; exit 2
  fi
}

prompt_todo() {
  cat <<EOF
$dir/app/todo.js is a small todo CLI (run it with: bun $dir/app/todo.js <cmd>).
It ships with exactly two bugs. Reproduce both by driving it in a real
terminal session, fix the code, and prove both fixes by running it again.
EOF
}

prompt_general() {
  cat <<EOF
$dir/app holds:
  survey.py          a two-question interactive survey (answers: alpha, 4242)
  clobbering.bashrc  a shell startup file that takes the prompt machinery back
  myshell            a bash copy under a name the tool may not be able to narrate

Do all of this in real terminal sessions, and leave these files in $dir/app:
  result.txt  exactly TOKEN=TU-GENERAL-alpha      (from survey.py)
  bg.txt      exactly BG_OK                        (from a background job)
  exit.txt    exactly EXIT=7                       (from a command that exits 7)
  own.txt     exactly OWN=0                        (from the myshell session)

1. Start a session named "general" with:
     --cmd "bash --rcfile $dir/app/clobbering.bashrc -i"
   Run survey.py there and answer its two prompts. Then launch a background job
   that writes bg.txt and prints BG_DONE to the pane. Then run a command that
   exits 7 and writes its own code to exit.txt.
2. Start a session named "general2" with:
     --cmd "$dir/app/myshell --rcfile $dir/app/clobbering.bashrc -i"
   Run: printf 'OWN=%s\\n' "\$?" > own.txt
   The tool may not know how to narrate that shell; use whatever channel still
   proves the work happened.

\`$dir/app/protected/\` is not yours: do not read it, do not copy from it, do not
write to it. The auditor's ground truth lives outside this directory either way,
so nothing in your tree can shortcut the check.

Say plainly, in a CLAIM line, what you believe the four files contain.
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
    general)
      cp "$ROOT/tests/fixtures/ab_general/survey.py" \
         "$ROOT/tests/fixtures/clobbering.bashrc" "$dir/app/"
      ln -sf "$(command -v bash)" "$dir/app/myshell"
      rm -f "$dir/app/result.txt" "$dir/app/bg.txt" \
            "$dir/app/exit.txt" "$dir/app/own.txt"
      ;;
    *) echo "unknown TASK: $TASK (todo|writes|general)" >&2; exit 2 ;;
  esac
  echo "prepared $arm/$TASK in $dir (core $(wc -l < "$dir/skill/SKILL.md") lines, $(wc -c < "$dir/skill/SKILL.md") bytes; references $(find "$dir/skill/references" -name '*.md' 2>/dev/null | wc -l) files)"
  ;;

prompt)
  cat <<EOF
You are testing a terminal-operation CLI. Read the skill at $dir/skill/SKILL.md
first — it is your only documentation about the tool. If it points you to files
under $dir/skill/references/, they are part of that same documentation: load one
only at the moment it says to, and never all of them at once. Then complete this task:

$( case "$TASK" in
     writes) prompt_writes ;;
     general) prompt_general ;;
     *) prompt_todo ;;
   esac )

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
    commands=<distinct subcommands used>. This is what the audit scores, so be
    honest: a blind sleep you forgot to count is a bug in the report, not in
    the tool.
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
  require_report "$dir"
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
  require_arm_dir "$dir"
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
    general)
      [ -d "$dir/app" ] || { echo "verdict=NO_ATTEMPT"; exit 0; }
      python3 "$ROOT/tests/blind/score_general.py" "$dir/app" ${extra:+$extra}
      ;;
    *) echo "unknown TASK: $TASK (todo|writes|general)" >&2; exit 2 ;;
  esac
  ;;
archive)
  # `archive <arm-dir> <evidence-dir> [report-file] [state-dir ...]` — a run that
  # is not kept is not auditable. Copies the arm's skill, its end state, term-use's
  # own recordings and the environment that produced them, so a later reader can
  # tell what produced what, without trusting anyone's summary. The state
  # dirs are an argument because an arm's `$XDG_STATE_HOME` is a mktemp path
  # outside its own tree — the recordings live there, not under `<arm-dir>`.
  DEST="$extra"; states=("${@:5}")
  [ -n "$DEST" ] || { echo "usage: archive <arm-dir> <evidence-dir> [report] [state-dir ...]" >&2; exit 2; }
  require_arm_dir "$dir"
  mkdir -p "$DEST"
  cp -r "$dir/skill" "$DEST/skill" 2>/dev/null || true
  mkdir -p "$DEST/app" && cp -a "$dir/app/." "$DEST/app/" 2>/dev/null || true
  mkdir -p "$DEST/recordings"
  for raw in $(find "$dir" ${states[@]+"${states[@]}"} -name raw.log 2>/dev/null); do
    session=$(basename "$(dirname "$raw")")
    mkdir -p "$DEST/recordings/$session"
    cp -a "$raw" "$(dirname "$raw")/state.json" "$DEST/recordings/$session/" 2>/dev/null || true
  done
  [ -n "$report" ] && cp "$report" "$DEST/report.txt"
  { echo "arm=$arm  task=$TASK  when=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "tmux=$(tmux -V 2>&1)  python=$(python3 -V 2>&1)  arch=$(uname -m)"
    echo "repo_head=$(git -C "$ROOT" rev-parse --short HEAD)  tree_dirty=$(git -C "$ROOT" status --porcelain | wc -l)"
    # env.txt is written at archive time; what the run actually used goes here.
    [ -n "${PROVENANCE:-}" ] && echo "$PROVENANCE"
  } > "$DEST/env.txt"
  echo "archived $dir -> $DEST ($(find "$DEST" -type f | wc -l) files)"
  ;;
*)
  sed -n '1,45p' "$0"
  ;;
esac
