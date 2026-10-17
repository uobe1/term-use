#!/usr/bin/env bash
# E2E-26: when the fact channel is structurally dead, say so — with a hint that
# is safe to follow.
#
# Two shapes, both reported from real use:
#   (a) the pane's foreground is `ssh`: OSC 133 answers come from the LOCAL
#       shell, which is blocked inside ssh, so --cmd-done can never fire.
#   (b) a prompt framework (starship/powerline) replaces PROMPT_COMMAND after
#       our injection: markers existed, then stop coming mid-session.
# Before this, both surfaced as a plain `timeout`, and --shell-ready's hint said
# "send the program's quit key" — which, followed against an idle remote shell,
# injects an interrupt into a session that was fine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
TD="python3 -m termuse"
SOCK="td26sock_$$_$RANDOM"
TMP="$(mktemp -d)"
export XDG_STATE_HOME="$TMP/state"
mkdir -p "$TMP/tmux"
export TMUX_TMPDIR="$TMP/tmux"

cleanup() { tmux -L "$SOCK" kill-server >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "E2E-26 FAIL: $*"; exit 1; }

no_quit_key_hint() {   # $1 = error json file
  # The hint may *forbid* a quit key (that is the point); it must not instruct
  # one, and it must name a channel that actually works in this shape.
  python3 -c "
import json, re
e = json.load(open('$1'))['error']
h = (e['hint'] or '').lower()
for m in re.finditer('quit key', h):
    before = h[max(0, m.start() - 20):m.start()]
    assert ('not ' in before or 'never ' in before), 'hint instructs a keystroke: ' + h
assert '--until' in h or '--since-send' in h or 'out' in h, \
    'hint must name a usable channel: ' + h
" || fail "unsafe hint (see above)"
}

# --- (a) ssh in the foreground ---------------------------------------------
if command -v ssh >/dev/null; then
  $TD start -n td26s --cmd bash --socket "$SOCK" --width 90 --height 24 >/dev/null \
    || fail "start failed"
  $TD wait -n td26s --shell-ready --timeout 15 >/dev/null || fail "local shell not ready"
  # 192.0.2.1 is RFC 5737 documentation space: unroutable by definition, so ssh
  # blocks in "connecting" and stays the pane's foreground program — the shape
  # of a live remote session, without needing a remote host.
  SSHCMD="ssh -o BatchMode=yes -o ConnectTimeout=60 -p 65500 192.0.2.1"
  need_ssh() {   # the precondition of every case below, re-checked not assumed:
    # ssh can give up early on some networks, and a case that runs after a burned
    # 6s timeout is 6s closer to that than the case before it.
    fc=$($TD screen -n td26s --meta | python3 -c 'import json,sys; print(json.load(sys.stdin).get("pane_current_command") or "")')
    if [ "$fc" != "ssh" ]; then
      $TD send -n td26s --type "$SSHCMD" --key Enter >/dev/null || fail "send ssh failed"
      for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        fc=$($TD screen -n td26s --meta | python3 -c 'import json,sys; print(json.load(sys.stdin).get("pane_current_command") or "")')
        [ "$fc" = "ssh" ] && break
        sleep 0.4
      done
    fi
    [ "$fc" = "ssh" ] || fail "could not keep ssh in the foreground (saw: $fc)"
  }
  $TD send -n td26s --type "$SSHCMD" --key Enter >/dev/null || fail "send ssh failed"
  need_ssh

  if $TD wait -n td26s --cmd-done --timeout 6 >/dev/null 2>"$TMP/a.json"; then
    fail "--cmd-done must not claim success while the local shell is blocked in ssh"
  fi
  python3 -c "
import json
e = json.load(open('$TMP/a.json'))['error']
assert e['code'] == 'shell-protocol-unavailable', e
assert 'ssh' in e['message'] + (e['hint'] or ''), f'name the foreground program: {e}'
assert '--until' in (e['hint'] or ''), f'point at a usable channel: {e}'
" || fail "ssh shape (see above)"
  no_quit_key_hint "$TMP/a.json"

  need_ssh                      # the case above burned 6s of it
  if $TD wait -n td26s --shell-ready --timeout 6 >/dev/null 2>"$TMP/b.json"; then
    fail "--shell-ready must not pretend under ssh"
  fi
  python3 -c "
import json
e = json.load(open('$TMP/b.json'))['error']
assert e['code'] == 'shell-protocol-unavailable', e
" || fail "shell-ready under ssh must be diagnosed, not 'shell-not-ready'"
  no_quit_key_hint "$TMP/b.json"

  # --done must not burn its whole timeout on a dead channel
  need_ssh                      # and the case above burned another 6s
  start=$(date +%s)
  $TD wait -n td26s --done --timeout 8 >/dev/null 2>"$TMP/c.json" || true
  took=$(( $(date +%s) - start ))
  python3 -c "
import json
e = json.load(open('$TMP/c.json'))['error']
assert e['code'] in ('shell-protocol-unavailable', 'timeout'), e
" || fail "done under ssh (see above)"
  [ "$took" -le 8 ] || fail "--done burned $took s"
  $TD stop -n td26s >/dev/null
fi

# --- (b) a prompt framework that replaces our hook -------------------------
mkdir -p "$TMP/home"
cat > "$TMP/home/.bashrc" <<'SH'
# stand-in for starship/powerline: rewrite PROMPT_COMMAND on every prompt
__mine() { PROMPT_COMMAND=""; PS1="own> "; }
PROMPT_COMMAND=__mine
SH
HOME="$TMP/home" $TD start -n td26h --cmd bash --socket "$SOCK" \
  --width 90 --height 24 >/dev/null || fail "start (clobbered hooks) failed"
sleep 1
$TD send -n td26h --type "echo HI_26" --key Enter >/dev/null
$TD wait -n td26h --until 'HI_26' --timeout 10 >/dev/null || fail "command never ran"
if $TD wait -n td26h --cmd-done --timeout 6 >/dev/null 2>"$TMP/d.json"; then
  fail "a dead marker channel must not report success"
fi
python3 -c "
import json
e = json.load(open('$TMP/d.json'))['error']
assert e['code'] == 'shell-protocol-unavailable', e
assert '--since-send' in (e['hint'] or '') or '--until' in (e['hint'] or ''), e
" || fail "clobbered-hooks shape (see above)"
no_quit_key_hint "$TMP/d.json"
$TD stop -n td26h >/dev/null

# --- (c) a NON-empty user PROMPT_COMMAND must not break the fact channel ----
# The bug this pins: splicing the user's PROMPT_COMMAND into ours emitted a
# second, corrupt D marker per prompt, and that stray marker (no preceding C)
# was read as "aborted" — masking the real done, so --cmd-done timed out in
# every session with a prompt framework (starship, atuin, history logging).
mkdir -p "$TMP/home2"
printf 'my_prompt() { :; }\nPROMPT_COMMAND="my_prompt"\nexport PS1="pc> "\n' > "$TMP/home2/.bashrc"
HOME="$TMP/home2" $TD start -n td26p --cmd bash --socket "$SOCK" \
  --width 90 --height 24 >/dev/null || fail "start (user PROMPT_COMMAND) failed"
$TD wait -n td26p --shell-ready --timeout 15 >/dev/null || fail "shell-ready with user PC"
$TD send -n td26p --type "echo PC_MARK; false" --key Enter >/dev/null
$TD wait -n td26p --done --expect-code 1 --timeout 15 | python3 -c '
import json, sys
v = json.load(sys.stdin)
# either fact channel is a correct proof; on tmux 3.8 the native status usually
# answers first, and both must report the same exit code
assert v["confidence"] == "fact" and v["via"] in ("osc133", "tmux-command-status"), v
assert v["exit_code"] == 1, f"the user PROMPT_COMMAND swallowed the exit code: {v}"
' || fail "fact channel must survive a user PROMPT_COMMAND (see above)"
$TD stop -n td26p >/dev/null

echo "E2E-26 PASS"
