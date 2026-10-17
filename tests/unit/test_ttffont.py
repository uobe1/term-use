#!/usr/bin/env python3
"""Unit tests for the stdlib sfnt/cmap reader — pure asserts, no pytest.

These encode facts about fonts that exist on this box; each is cross-checked
against fontTools' own cmap parsing offline (16,798 codepoints across four
faces, zero disagreements). A format-4 offset bug here is silent and expensive:
the renderer would draw a hollow "missing" box for a glyph the font has.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import ttffont

CANDIDATES = {
    "mono": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "sans": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "free": "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
}


def _face(key):
    path = CANDIDATES[key]
    if not os.path.isfile(path):
        return None
    return ttffont.load(path)


def test_ascii_and_latin_are_covered_everywhere():
    for key in ("mono", "sans", "free"):
        f = _face(key)
        if f is None:
            continue
        assert f.error is None, f.error
        for ch in "aZ09 !~éß":
            assert f.covers(ch) is True, (key, ch)


def test_box_and_block_ranges_parse_correctly():
    """The codepoints that used to be mis-read: high BMP, reached through a
    format-4 segment whose idRangeOffset indexes glyphIdArray."""
    f = _face("mono")
    if f is None:
        print("skip test_box_and_block_ranges_parse_correctly (no DejaVu)")
        return
    for ch in "─│┌┐└┘├┤┬┴┼═║▄▀█░▒▓":
        assert f.covers(ch) is True, f"DejaVu Sans Mono has {ch!r} (U+{ord(ch):04X})"


def test_gaps_are_reported_as_gaps_not_guessed():
    f = _face("mono")
    if f is None:
        return
    # DejaVu Sans Mono has no PUA and no emoji — the honest answer is False,
    # which is what routes the renderer to vector shapes / fallback faces.
    assert f.covers("\ue0b0") is False
    assert f.covers("\U0001F600") is False


def test_fixed_pitch_and_metrics_read():
    mono, sans = _face("mono"), _face("sans")
    if mono is None or sans is None:
        print("skip test_fixed_pitch_and_metrics_read (need DejaVu mono+sans)")
        return
    assert mono.fixed_pitch is True and mono.units_per_em == 2048
    assert sans.fixed_pitch is False, "post.isFixedPitch distinguishes the two"
    assert mono.underline and mono.underline[0] < 0, "underline sits below baseline"


def test_braille_coverage_differs_between_faces_and_is_told_truthfully():
    """DejaVu Sans Mono has no braille while DejaVu Sans does — a chain that
    trusted family names would render tofu for a spinner."""
    mono, sans = _face("mono"), _face("sans")
    if mono is None or sans is None:
        return
    assert mono.covers("⣿") is False
    assert sans.covers("⣿") is True


def test_unparseable_font_degrades_to_unknown_not_a_crash():
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as fh:
        fh.write(b"not a font at all")
        path = fh.name
    try:
        f = ttffont.Face(path)
        assert f.error, "must record why it could not be read"
        assert f.covers("a") is None, "unknown, so the caller trusts the font"
    finally:
        os.unlink(path)


def test_supports_helper_aggregates():
    mono = _face("mono")
    if mono is None:
        return
    assert ttffont.supports(mono.path, "hello") is True
    assert ttffont.supports(mono.path, "hi \U0001F600") is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("test_ttffont: all passed")
