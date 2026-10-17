#!/usr/bin/env python3
"""Unit tests for context accounting — pure asserts, no pytest."""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import budget


def _env_none():
    old = budget.os.environ.pop("TERM_USE_CONTEXT_BUDGET", None)
    return old


def _env_restore(old):
    if old is None:
        budget.os.environ.pop("TERM_USE_CONTEXT_BUDGET", None)
    else:
        budget.os.environ["TERM_USE_CONTEXT_BUDGET"] = old


def test_default_budget_and_env_override():
    old = _env_none()
    try:
        assert budget.budget() == budget.DEFAULT_BUDGET_TOKENS
        budget.os.environ["TERM_USE_CONTEXT_BUDGET"] = "500"
        assert budget.budget() == 500
        budget.os.environ["TERM_USE_CONTEXT_BUDGET"] = "garbage"
        assert budget.budget() == budget.DEFAULT_BUDGET_TOKENS, "junk must not disable accounting"
        budget.os.environ["TERM_USE_CONTEXT_BUDGET"] = "0"
        assert budget.budget() == 0, "0 is a real setting: never nag, still measure"
    finally:
        _env_restore(old)


def test_text_context_is_json_safe_and_labeled():
    ctx = budget.text_context("hello\n\nworld  \n")
    assert ctx["bytes"] == 15
    assert ctx["lines"] == 3 and ctx["nonblank_lines"] == 2
    assert ctx["est_tokens"] == 4  # ceil(15/4)
    assert ctx["over_budget"] is False
    assert ctx["method"], "an estimate must say how it was estimated"
    json.dumps(ctx)  # the whole point: this rides in every verdict


def test_big_capture_flags_over_budget():
    old = _env_none()
    try:
        budget.os.environ["TERM_USE_CONTEXT_BUDGET"] = "10"
        ctx = budget.text_context("x" * 200)
        assert ctx["over_budget"] is True
        assert budget.est_text_tokens(200) == 50
    finally:
        _env_restore(old)


def test_warn_goes_to_stderr_not_stdout():
    old = _env_none()
    out, err = io.StringIO(), io.StringIO()
    saved = (sys.stdout, sys.stderr)
    try:
        budget.os.environ["TERM_USE_CONTEXT_BUDGET"] = "10"
        sys.stdout, sys.stderr = out, err
        fired = budget.warn(budget.text_context("y" * 400), "sess")
    finally:
        sys.stdout, sys.stderr = saved
        _env_restore(old)
    assert fired is True
    assert out.getvalue() == "", "stdout must stay pipeable"
    msg = err.getvalue()
    assert "tokens" in msg and "budget" in msg and "clear -n sess" in msg, msg


def test_warn_silent_under_budget():
    old = _env_none()
    err = io.StringIO()
    saved = sys.stderr
    try:
        sys.stderr = err
        fired = budget.warn(budget.text_context("tiny"), "sess")
    finally:
        sys.stderr = saved
        _env_restore(old)
    assert fired is False and err.getvalue() == ""


def test_image_context_uses_pixels_and_reports_size():
    import os
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(b"0" * 1234)
        path = fh.name
    try:
        ctx = budget.image_context(path, [1000, 570])
        assert ctx["image_bytes"] == 1234
        assert ctx["pixels"] == [1000, 570]
        assert ctx["est_tokens"] == budget.est_image_tokens([1000, 570])
        assert "method" in ctx
        missing = budget.image_context(path + ".nope", [10, 10])
        assert missing["image_bytes"] == 0, "a vanished file must not crash accounting"
    finally:
        os.unlink(path)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("test_budget: all passed")
