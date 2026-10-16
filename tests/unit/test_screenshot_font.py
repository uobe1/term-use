#!/usr/bin/env python3
"""Unit tests for the screenshot font probe — pure asserts, no pytest."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse.errors import TDError
from termuse.screenshot import find_font, rank_mono, rank_style, select_mono


def _touch(tmp, names):
    dirs = []
    for root, files in names.items():
        d = Path(tmp) / root
        d.mkdir(parents=True)
        for n in files:
            (d / n).write_bytes(b"")
        dirs.append(str(d))
    return dirs


def test_select_mono_prefers_known_readable_mono():
    """A box with several monos must not settle for an arbitrary one by glob order."""
    with tempfile.TemporaryDirectory() as tmp:
        dirs = _touch(tmp, {"fonts": [
            "DejaVuSansMono.ttf", "ZebraMono.ttf", "FreeMono.otf",
            "NotoSansMonoCJKjp.ttf",
        ]})
        assert select_mono(dirs).endswith("DejaVuSansMono.ttf")


def test_select_mono_never_picks_a_bold_cut():
    """Real finding: dictionary order put DejaVuSansMono-Bold.ttf first, so every
    cell rendered thick. Base weight must beat the styled cuts."""
    with tempfile.TemporaryDirectory() as tmp:
        dirs = _touch(tmp, {"fonts": [
            "DejaVuSansMono-Bold.ttf", "DejaVuSansMono-BoldOblique.ttf",
            "DejaVuSansMono-Oblique.ttf", "DejaVuSansMono.ttf",
        ]})
        assert select_mono(dirs).endswith("DejaVuSansMono.ttf")


def test_explicit_regular_beats_bare_filename():
    """Same family: an explicit Regular cut outranks the unmarked file."""
    with tempfile.TemporaryDirectory() as tmp:
        dirs = _touch(tmp, {"fonts": ["NotoSansMono.ttf", "NotoSansMono-Regular.ttf"]})
        assert select_mono(dirs).endswith("NotoSansMono-Regular.ttf")


def test_freefont_style_order():
    """freefont spells weight inside the name (FreeMonoBold.ttf), no suffix dot."""
    with tempfile.TemporaryDirectory() as tmp:
        dirs = _touch(tmp, {"fonts": [
            "FreeMonoBoldOblique.ttf", "FreeMonoBold.ttf",
            "FreeMonoOblique.ttf", "FreeMono.ttf",
        ]})
        assert select_mono(dirs).endswith("FreeMono.ttf")


def test_rank_style_orders_base_over_styled():
    assert rank_style("NotoSansMono-Regular.ttf") < rank_style("NotoSansMono.ttf")
    assert rank_style("NotoSansMono.ttf") < rank_style("NotoSansMono-Bold.ttf")
    assert rank_style("NotoSansMono.ttf") < rank_style("FreeMonoBoldOblique.ttf")


def test_select_mono_falls_back_alphabetically():
    with tempfile.TemporaryDirectory() as tmp:
        dirs = _touch(tmp, {"fonts": ["ZebraMono.ttf", "Amono.ttf"]})
        assert select_mono(dirs).endswith("Amono.ttf")


def test_select_mono_ignores_non_mono():
    """Proportional fallbacks render a wrong-looking screen; refuse them."""
    with tempfile.TemporaryDirectory() as tmp:
        dirs = _touch(tmp, {"fonts": ["FreeSans.ttf", "DejaVuSans.ttf"]})
        assert select_mono(dirs) is None


def test_select_mono_searches_nested_and_later_dirs():
    with tempfile.TemporaryDirectory() as tmp:
        first = _touch(tmp, {"a": ["NestedMono.ttf"]})
        second = _touch(tmp, {"b/deep": ["OtherMono.ttf"]})
        assert select_mono(first + second) in (
            os.path.join(first[0], "NestedMono.ttf"),
            os.path.join(second[0], "OtherMono.ttf"),
        )
        assert select_mono(second) == os.path.join(second[0], "OtherMono.ttf")


def test_rank_mono_orders_by_preference():
    assert rank_mono("DejaVuSansMono.ttf") < rank_mono("ZebraMono.ttf")
    assert rank_mono("DejaVuSansMono-Bold.ttf") == rank_mono("DejaVuSansMono.ttf") \
        or rank_mono("DejaVuSansMono-Bold.ttf") < rank_mono("ZebraMono.ttf")


def test_termux_paths_keep_priority_over_linux_dirs():
    """The Android/Termux probes are the author's target environment; the new
    Linux probe must only ever run after them, never reorder them."""
    if os.path.isdir("/system/fonts"):
        print("skip test_termux_paths_keep_priority_over_linux_dirs (real Termux/Android)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("PREFIX")
        os.environ["PREFIX"] = tmp
        try:
            _touch(tmp, {"share/fonts/dejavu": ["DejaVuSansMono.ttf"]})
            assert find_font().endswith("DejaVuSansMono.ttf"), "Termux hit must win"
            assert find_font().startswith(tmp)
        finally:
            if old is None:
                del os.environ["PREFIX"]
            else:
                os.environ["PREFIX"] = old


def test_term_use_font_override_is_honored():
    with tempfile.TemporaryDirectory() as tmp:
        font = Path(tmp) / "My.ttf"
        font.write_bytes(b"")
        old = os.environ.get("TERM_USE_FONT")
        os.environ["TERM_USE_FONT"] = str(font)
        try:
            assert find_font() == str(font)
        finally:
            if old is None:
                del os.environ["TERM_USE_FONT"]
            else:
                os.environ["TERM_USE_FONT"] = old


def test_bad_override_raises_structured_error():
    """A typo'd override must say so, not silently auto-probe and confuse later."""
    old = os.environ.get("TERM_USE_FONT")
    os.environ["TERM_USE_FONT"] = "/no/such/Mono.ttf"
    try:
        find_font()
        assert False, "expected TDError for a nonexistent TERM_USE_FONT"
    except TDError as e:
        assert e.code == "font-missing", e.to_json()
        assert "TERM_USE_FONT" in e.message + (e.hint or "")
    finally:
        if old is None:
            del os.environ["TERM_USE_FONT"]
        else:
            os.environ["TERM_USE_FONT"] = old


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("test_screenshot_font: all passed")
