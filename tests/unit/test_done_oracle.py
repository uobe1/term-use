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


def test_every_oracle_has_a_via_label():
    for oracle in ("osc133", "probe", "pane-exit", "quiet", "quiet-unmanaged"):
        assert oracle in waiting.DONE_VIA, oracle
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("test_done_oracle: all passed")
