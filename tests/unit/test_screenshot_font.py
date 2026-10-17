#!/usr/bin/env python3
"""Unit tests for the screenshot font probe — pure asserts, no pytest."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import screenshot as tdscreenshot
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


def test_font_dirs_follows_conventions_not_one_device():
    """Probe order must be environment-driven. The old code hardcoded the
    Android/Termux layout first (and a com.termux path as the default PREFIX),
    which reads as "this works on the author's phone" to everyone else."""
    old = {k: os.environ.get(k) for k in
           ("TERM_USE_FONT_DIRS", "PREFIX", "XDG_DATA_HOME", "SYSROOT",
            "CONDA_PREFIX", "HOME")}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ.pop("TERM_USE_FONT_DIRS", None)
            os.environ["PREFIX"] = os.path.join(tmp, "prefix")
            os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "xdg")
            dirs = tdscreenshot.font_dirs()
            assert os.path.join(tmp, "prefix", "share", "fonts") in dirs, dirs
            assert os.path.join(tmp, "xdg", "fonts") in dirs, dirs
            assert "/usr/share/fonts" in dirs, dirs
            assert not any("com.termux" in d for d in dirs), dirs
            assert dirs[0].startswith(tmp), f"my env must be probed first: {dirs}"
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def test_explicit_font_dirs_replaces_the_probe_list():
    """TERM_USE_FONT_DIRS is an instruction, not a hint: an empty one must not
    quietly fall back to the system fonts."""
    old = os.environ.get("TERM_USE_FONT_DIRS")
    try:
        os.environ["TERM_USE_FONT_DIRS"] = "/no/such/dir" + os.pathsep + "/also/nope"
        assert tdscreenshot.font_dirs() == ["/no/such/dir", "/also/nope"]
        assert tdscreenshot.select_mono(tdscreenshot.font_dirs()) is None
        # an explicit dir list overrides the probe, but not the bundled last
        # resort — and the answer must say which face it fell back to
        assert tdscreenshot.find_font() == (tdscreenshot.BUNDLED_FONT, "bundled")
    finally:
        if old is None:
            del os.environ["TERM_USE_FONT_DIRS"]
        else:
            os.environ["TERM_USE_FONT_DIRS"] = old


def test_prefix_share_fonts_tree_is_discoverable():
    with tempfile.TemporaryDirectory() as tmp:
        dirs = _touch(tmp, {"prefix/share/fonts/dejavu": ["DejaVuSansMono.ttf"]})
        found = tdscreenshot.select_mono([os.path.join(tmp, "prefix/share/fonts")])
        assert found and found.endswith("DejaVuSansMono.ttf"), (dirs, found)


def test_monospace_families_without_the_word_mono_are_still_found():
    """Menlo/Consolas/Cascadia are monospace; refusing them because of a naming
    habit would kill the image channel on macOS by default."""
    with tempfile.TemporaryDirectory() as tmp:
        dirs = _touch(tmp, {"fonts": ["Menlo-Regular.ttf"]})
        assert tdscreenshot.is_mono_name(os.path.join(dirs[0], "Menlo-Regular.ttf"))
        assert tdscreenshot.select_mono(dirs).endswith("Menlo-Regular.ttf")


def test_term_use_font_override_is_honored():
    with tempfile.TemporaryDirectory() as tmp:
        font = Path(tmp) / "My.ttf"
        font.write_bytes(b"")
        old = os.environ.get("TERM_USE_FONT")
        os.environ["TERM_USE_FONT"] = str(font)
        try:
            assert find_font() == (str(font), "override")
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


class _StubFace:
    """A cmap answer by filename, so the *ranking* is tested without a font."""

    def __init__(self, has):
        self.has = set(has)

    def covers(self, ch):
        return ch in self.has


def _with_font_dir(tmpdir, coverage):
    """Point the probe at one directory of empty font files and answer coverage
    from `coverage` instead of parsing them (the ranking is what is under test)."""
    old_dirs = os.environ.get("TERM_USE_FONT_DIRS")
    real_load = tdscreenshot.ttffont.load
    os.environ["TERM_USE_FONT_DIRS"] = tmpdir
    tdscreenshot.ttffont.load = lambda p: _StubFace(coverage.get(Path(p).name, ""))

    def restore():
        tdscreenshot.ttffont.load = real_load
        if old_dirs is None:
            os.environ.pop("TERM_USE_FONT_DIRS", None)
        else:
            os.environ["TERM_USE_FONT_DIRS"] = old_dirs
    return restore


def test_fallback_is_chosen_by_coverage_not_by_filename():
    """The measured failure of issue #3: seven `unifont*` files matched the
    strongest hint and filled all four slots, so `wqy-zenhei.ttc` — 34th in the
    listing — was never opened and CJK came out as an 8x16 bitmap or tofu."""
    with tempfile.TemporaryDirectory() as tmp:
        names = ["unifont-15.1.05.otf", "unifont_upper-15.1.05.otf",
                 "unifont_jp-15.1.05.otf", "DejaVuSansMono.ttf",
                 "wqy-zenhei.ttc"]
        for n in names:
            (Path(tmp) / n).write_bytes(b"")
        restore = _with_font_dir(tmp, {"wqy-zenhei.ttc": "你好",
                                       "unifont-15.1.05.otf": "⣿"})
        try:
            chosen = tdscreenshot.fallback_fonts(
                os.path.join(tmp, "DejaVuSansMono.ttf"), {"你", "好"})
            assert [os.path.basename(c) for c in chosen] == ["wqy-zenhei.ttc"], chosen
        finally:
            restore()


def test_one_family_occupies_one_slot():
    """`unifont`, `unifont_upper`, `unifont_jp` are three coverages; seven cuts of
    the same family are one. Versions and style words must not multiply it, and a
    Bold cut must never win — the grid already carries bold as an attribute."""
    with tempfile.TemporaryDirectory() as tmp:
        names = ["NotoSansCJK-Regular.ttc", "NotoSansCJK-Bold.ttc",
                 "NotoSansCJKjp-Regular.ttc", "DejaVuSansMono.ttf"]
        for n in names:
            (Path(tmp) / n).write_bytes(b"")
        # The Noto CJK family covers nothing here, so the search must move on to
        # the next family instead of spending its budget on siblings of the first.
        restore = _with_font_dir(tmp, {"NotoSansCJKjp-Regular.ttc": "你"})
        try:
            chosen = [os.path.basename(c) for c in tdscreenshot.fallback_fonts(
                os.path.join(tmp, "DejaVuSansMono.ttf"), {"你"})]
            assert chosen == ["NotoSansCJKjp-Regular.ttc"], chosen
            assert not any("Bold" in c for c in chosen), chosen
        finally:
            restore()


def test_a_screen_that_needs_nothing_opens_nothing():
    """Every fallback face costs a cmap parse and a Pillow load. An ASCII screen
    has no gap to fill, so the chain stays the primary alone."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "wqy-zenhei.ttc").write_bytes(b"")
        (Path(tmp) / "DejaVuSansMono.ttf").write_bytes(b"")
        restore = _with_font_dir(tmp, {})
        try:
            assert tdscreenshot.fallback_fonts(
                os.path.join(tmp, "DejaVuSansMono.ttf"), set()) == []
        finally:
            restore()


def test_no_coverage_question_keeps_the_symbol_pack_order():
    """`need=None` is the old unranked call: symbol packs and Unifont first,
    because that is what they exist for."""
    with tempfile.TemporaryDirectory() as tmp:
        for n in ("wqy-zenhei.ttc", "unifont-15.1.05.otf", "DejaVuSansMono.ttf"):
            (Path(tmp) / n).write_bytes(b"")
        restore = _with_font_dir(tmp, {})
        try:
            chosen = [os.path.basename(c) for c in tdscreenshot.fallback_fonts(
                os.path.join(tmp, "DejaVuSansMono.ttf"), None)]
            assert chosen[0] == "unifont-15.1.05.otf", chosen
        finally:
            restore()


def test_droid_fallback_is_a_candidate_at_all():
    """Issue #3 came from an Android device whose only CJK face was
    `DroidSansFallback.ttf` — a name the hint list did not contain, so the one
    font that could have drawn the screen was filtered out before ranking."""
    with tempfile.TemporaryDirectory() as tmp:
        for n in ("DroidSansMono.ttf", "DroidSansFallback.ttf",
                  "DejaVuSansMono.ttf"):
            (Path(tmp) / n).write_bytes(b"")
        restore = _with_font_dir(tmp, {"DroidSansFallback.ttf": "你"})
        try:
            chosen = tdscreenshot.fallback_fonts(
                os.path.join(tmp, "DroidSansMono.ttf"), {"你"})
            assert [os.path.basename(c) for c in chosen] == ["DroidSansFallback.ttf"], chosen
        finally:
            restore()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("test_screenshot_font: all passed")
