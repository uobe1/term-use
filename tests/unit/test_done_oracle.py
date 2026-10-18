#!/usr/bin/env python3
"""Unit tests for `wait --done` oracle selection — pure asserts, no pytest.

The point of --done is that the agent does not have to know which evidence its
target can produce; that only works if the selection rule is exact, so it is
tested here as a function instead of only through e2e timing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import waiting


def test_program_is_pane_only_for_non_shells():
    assert waiting.program_is_pane("nano") is True
    assert waiting.program_is_pane("python3 -c pass") is True
    assert waiting.program_is_pane("/usr/bin/htop") is True
    assert waiting.program_is_pane("bash") is False
    assert waiting.program_is_pane("/bin/bash") is False
    assert waiting.program_is_pane("zsh") is False
    assert waiting.program_is_pane("sh") is False
    assert waiting.program_is_pane("login") is False
    assert waiting.program_is_pane(None) is False
    assert waiting.program_is_pane("") is False
    # a shell *option* must not be mistaken for a program
    assert waiting.program_is_pane("bash --rcfile /tmp/rc") is False


def test_done_oracle_prefers_the_shell_protocol():
    managed = {"shell_integration": True, "cmd": "bash", "last_send_offset": 812}
    assert waiting.choose_done_oracle(managed) == "osc133"
    # Nothing sent yet: no fresh D marker can ever arrive, so the probe is the
    # strongest honest answer (the documented bare --cmd-done footgun).
    fresh = {"shell_integration": True, "cmd": "bash", "last_send_offset": 0}
    assert waiting.choose_done_oracle(fresh) == "probe"


def test_done_oracle_degrades_instead_of_refusing():
    assert waiting.choose_done_oracle(None) == "quiet-unmanaged"
    prog = {"shell_integration": False, "cmd": "nano"}
    assert waiting.choose_done_oracle(prog) == "pane-exit"
    plain_sh = {"shell_integration": False, "cmd": "sh", "last_send_offset": 40}
    assert waiting.choose_done_oracle(plain_sh) == "quiet"


def test_a_shell_that_cannot_report_can_still_be_asked():
    """`sh`/`dash` have no pre-exec hook, so there is nothing to inject — but the
    `true` probe can carry term-use's own completion sentinel, which is a fact
    channel. Settling for sampling there was the concession this replaces; a
    program that IS the pane still outranks it (pane death is the better fact)."""
    plain_sh = {"shell_integration": False, "cmd": "sh", "last_send_offset": 40}
    assert waiting.choose_done_oracle(plain_sh) == "quiet", "not told it is trackable"
    assert waiting.choose_done_oracle(plain_sh, trackable=True) == "probe"
    prog = {"shell_integration": False, "cmd": "bash deploy.sh"}
    assert waiting.choose_done_oracle(prog, trackable=True) == "pane-exit"
    assert waiting.DONE_VIA["probe"] == "shell-ready-probe", "the answer names its oracle"
    # A probe is a *new* command; it cannot answer for a line whose own marker
    # was skipped or paused. Sampling is the honest fallback there.
    for marker in ("skipped", "paused"):
        st = {"shell_integration": False, "cmd": "sh", "last_send_offset": 40,
              "marker_source": marker}
        assert waiting.choose_done_oracle(st, trackable=True) == "no-marker", marker


def test_a_send_without_its_own_marker_has_no_fact_oracle():
    """Paused/skipped/none sends cannot borrow an earlier completion. The
    display-stability oracle is the honest fallback, even on an integrated
    shell whose previous command left a marker in the window."""
    for marker in ("paused", "skipped", "none"):
        st = {"shell_integration": True, "cmd": "bash",
              "last_send_offset": 40, "marker_source": marker}
        assert waiting.choose_done_oracle(st) == "no-marker", marker
        assert waiting.choose_done_oracle(
            {**st, "native_available": True,
             "native_baseline": {"status": 0, "running": 0}},
            trackable=True) == "no-marker", marker


def test_a_sentinel_send_is_answered_by_the_recording_without_native():
    st = {"shell_integration": False, "cmd": "bash",
          "last_send_offset": 40, "marker_source": "sentinel"}
    assert waiting.choose_done_oracle(st) == "osc133"
    assert waiting.choose_done_oracle({**st, "last_send_offset": 0}) == "probe"


def test_native_command_status_is_a_first_class_oracle():
    """On 3.8 tmux answers by start time, and that channel survives a detached
    recorder and a shell `start` could not inject into. It must be chosen even
    when `shell_integration` is false — otherwise the native answer is only
    reachable through --cmd-done while --done silently samples the screen."""
    native_ready = {
        "shell_integration": False, "cmd": "sh", "last_send_offset": 40,
        "native_available": True,
        "native_baseline": {"status": 0, "running": 0, "prompt_time": None},
        "marker_source": "shell",
    }
    assert waiting.choose_done_oracle(native_ready) == "native"
    assert waiting.DONE_VIA["native"] == "tmux-command-status"
    # The native channel outranks the byte scan even when the shell has its
    # own integration: it binds by start time, the scan binds by file position.
    assert waiting.choose_done_oracle(
        {**native_ready, "shell_integration": True}) == "native"
    # Nothing sent yet: the probe is the honest first question, and it can use
    # the same native channel.
    fresh = {**native_ready, "last_send_offset": 0}
    assert waiting.choose_done_oracle(fresh) == "probe"
    # A sentinel send creates markers tmux will see even when the recorder and
    # the shell's own prompt machinery are both blind.
    sentinel = {**native_ready, "marker_source": "sentinel",
                "native_baseline": {"status": None, "running": 0,
                                    "prompt_time": None}}
    assert waiting.choose_done_oracle(sentinel) == "native"
    # No native evidence and no sentinel: quiet sampling is still the honest
    # fallback, not a fact claim that can never arrive.
    no_evidence = {**native_ready, "marker_source": "shell",
                   "native_baseline": {"status": None, "running": 0,
                                       "prompt_time": None}}
    assert waiting.choose_done_oracle(no_evidence) == "quiet"
    # A program that is the pane still outranks the native channel: its death
    # is the completion event, not the shell's last command status.
    prog = {**native_ready, "cmd": "bash deploy.sh"}
    assert waiting.choose_done_oracle(prog) == "pane-exit"


def test_every_oracle_has_a_via_label():
    for oracle in ("osc133", "native", "probe", "pane-exit", "quiet",
                   "quiet-unmanaged", "no-marker"):
        assert oracle in waiting.DONE_VIA, oracle
    assert waiting.DONE_VIA["no-marker"] == "no-marker"
    assert waiting.DONE_VIA["quiet-unmanaged"] != waiting.DONE_VIA["quiet"], \
        "an unmanaged wait must not look like a managed one"


def test_advice_names_the_stronger_channel():
    adv = waiting.oracle_advice(None, "until")
    assert adv and "unmanaged" in adv and "start" in adv
    managed_bash = {"shell_integration": True}
    adv = waiting.oracle_advice(managed_bash, "until")
    assert "--done" in adv and "fact" in adv and "--since-send" in adv
    no_si = {"shell_integration": False}
    adv = waiting.oracle_advice(no_si, "until")
    assert adv and "--cmd bash" in adv
    adv = waiting.oracle_advice(no_si, "quiet")
    assert adv and "heuristic" in adv
    # the fact oracle itself needs no second-guessing
    assert waiting.oracle_advice(managed_bash, "osc133") is None


def test_until_regex_serializes_its_scope():
    cond = waiting.UntilRegex("x", 0, "s", managed=True, scope="since-send")
    j = cond.to_json(True)
    assert j["scope"] == "since-send", j
    assert waiting.UntilRegex("x").to_json(True)["scope"] == "screen"


def test_shell_that_runs_a_script_is_the_program():
    """`bash script.sh` / `sh -c '…'` exit with their work: pane death is the
    fact oracle there, and the old name-only match used to steal the argument."""
    assert waiting.shell_owns_the_pane("bash") is True
    assert waiting.shell_owns_the_pane("/bin/bash -i") is True
    assert waiting.shell_owns_the_pane("bash --rcfile /tmp/rc -i") is True
    assert waiting.shell_owns_the_pane("zsh") is True
    # `start` rewrites zsh into an env-wrapped command: the wrapper must not
    # turn the shell into "the program that is the pane".
    assert waiting.shell_owns_the_pane("env ZDOTDIR=/x zsh") is True
    assert waiting.program_is_pane("env ZDOTDIR=/x zsh") is False
    assert waiting.shell_owns_the_pane("bash /tmp/paint.sh") is False
    assert waiting.shell_owns_the_pane("sh -c 'sleep 1'") is False
    assert waiting.shell_owns_the_pane("bash -c x") is False
    assert waiting.program_is_pane("bash /tmp/paint.sh") is True
    assert waiting.program_is_pane("bash") is False
    assert waiting.program_is_pane("nano") is True
    # the oracle for a script-runner is pane death, not quiet sampling
    st = {"shell_integration": False, "cmd": "bash /tmp/paint.sh"}
    assert waiting.choose_done_oracle(st) == "pane-exit"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("test_done_oracle: all passed")
