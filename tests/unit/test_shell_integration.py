#!/usr/bin/env python3
"""Unit tests for shell-integration injection + evidence-based detection."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import rcfile, records


def test_zsh_files_cover_the_startup_set():
    files = rcfile.zsh_integration_files()
    assert set(files) == {"zshenv", "zprofile", "zlogin", "zshrc"}, set(files)
    for name, body in files.items():
        assert "__FILE__" not in body, "template placeholder leaked"
        assert ".." + name not in body, "the dotted name must be added once"
        assert f".{name}" in body, f"{name} must source the user's own .{name}"


def test_zshrc_layers_our_hooks_after_the_user_rc():
    body = rcfile.zsh_integration_files()["zshrc"]
    assert body.index(".zshrc") < body.index("add-zsh-hook"), \
        "installing hooks before sourcing would let the user's rc clobber ours"
    assert "add-zsh-hook precmd __tu_precmd" in body
    assert "add-zsh-hook preexec __tu_preexec" in body
    assert "local __tu_st=$?" in body, "the exit code must be read first"
    assert "133;D;%s" in body and "133;C" in body and "133;B" in body
    assert "__TU_ZSH_INTEGRATION" in body, "double-injection must be guarded"


def test_zshrc_keeps_the_users_history_file():
    """A ZDOTDIR hijack relocates history by default; silently moving a user's
    history file is exactly the side effect that destroys trust in injection."""
    body = rcfile.zsh_integration_files()["zshrc"]
    assert 'HISTFILE="$__tu_user_zdotdir/.zsh_history"' in body


def test_bashrc_sources_by_convention_not_by_device():
    body = rcfile.bashrc_template()
    assert "com.termux" not in body and "/data/data" not in body
    for path in ("/etc/bash.bashrc", "/etc/bashrc", "$HOME/.bashrc"):
        assert path in body, path
    assert "PREFIX" in body, "prefix-style installs must be honoured generically"
    assert body.index(".bashrc") < body.index("PROMPT_COMMAND="), \
        "the user's PROMPT_COMMAND must be wrapped, not replaced"


def test_detect_shell_integration_matches_how_the_log_stores_escapes():
    """raw.log is JSON lines, and JSON escapes control characters even with
    ensure_ascii=False — detection must look for the form that is really there."""
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = str(Path(tmp) / "state")
        try:
            w = records.V2Writer.fresh("fishlike", 80, 24, "fish", None)
            w.append("o", "\x1b]133;A;click_events=1\x07prompt$ ")
            raw = (records.session_dir("fishlike") / "raw.log").read_text()
            assert "\\u001b]133;A" in raw, "documented storage form changed"
            assert records.detect_shell_integration("fishlike") is True

            v = records.V2Writer.fresh("plaindash", 80, 24, "dash", None)
            v.append("o", "$ ")
            assert records.detect_shell_integration("plaindash") is False

            n = records.V2Writer.fresh("vscode", 80, 24, "bash", None)
            n.append("o", "\x1b]633;D;0\x07")
            assert records.detect_shell_integration("vscode") is True

            assert records.detect_shell_integration("no-such-session") is False
        finally:
            if old is None:
                del os.environ["XDG_STATE_HOME"]
            else:
                os.environ["XDG_STATE_HOME"] = old


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("test_shell_integration: all passed")
