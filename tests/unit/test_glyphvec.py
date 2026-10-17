#!/usr/bin/env python3
"""Unit tests for the vectorized symbol ranges — pure asserts, no pytest.

`draw_cell` claims a cell only when it can paint it, and the renderer consults
the font chain otherwise. Two failure shapes are pinned here: claiming a range it
cannot draw (a cell comes out half-painted and the font never gets a turn), and
refusing a range a font on this machine may not have either (issue #3's `←`
rendered as tofu on the reporter's device).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from termuse import glyphvec

FG, BG = (255, 255, 255), (0, 0, 0)


class Recorder:
    def __init__(self):
        self.lines, self.rects, self.ellipses, self.polys = [], [], [], []

    def line(self, xy, fill=None, width=1):
        self.lines.append((list(xy), fill, width))

    def rectangle(self, xy, fill=None, outline=None):
        self.rects.append((list(xy), fill, outline))

    def ellipse(self, xy, fill=None):
        self.ellipses.append((list(xy), fill))

    def polygon(self, xy, fill=None):
        self.polys.append((list(xy), fill))


def paint(ch, w=10, h=19):
    d = Recorder()
    return glyphvec.draw_cell(ch, 0, 0, w, h, FG, BG, d), d


def endpoints(xy):
    """A Pillow line is a flat [x1, y1, x2, y2]."""
    return (xy[0], xy[1]), (xy[2], xy[3])


def test_every_arrow_paints_itself():
    for cp, dirs in glyphvec._ARROW_VECS.items():
        ok, d = paint(chr(cp))
        assert ok, f"U+{cp:04X} fell through to the font"
        # one shaft + two barbs per head
        assert len(d.lines) == 3 * len(dirs), (chr(cp), len(d.lines))
        assert all(fill == FG for _xy, fill, _w in d.lines)


def test_arrow_shaft_is_horizontal_and_its_head_reaches_the_edge():
    ok, d = paint("←")
    (x1, y1), (x2, y2) = endpoints(d.lines[0][0])
    assert y1 == y2, f"a left arrow is horizontal: {(x1, y1, x2, y2)}"
    tip = (x1, y1) if x1 < x2 else (x2, y2)
    assert tip[0] <= 1, f"the head must reach the left edge: {(x1, y1, x2, y2)}"


def test_double_arrow_has_two_half_shafts_from_the_centre():
    ok, d = paint("↔")
    cx = (10 - 1) / 2
    shafts = [xy for xy, _f, _w in d.lines
              if endpoints(xy)[0][1] == endpoints(xy)[1][1]]
    assert len(shafts) == 2, f"↔ is two horizontal shafts plus barbs: {shafts}"
    for xy in shafts:
        (x1, y1), (x2, y2) = endpoints(xy)
        assert min(abs(x1 - cx), abs(x2 - cx)) <= 1, \
            f"each half of ↔ starts at the centre: {(x1, y1, x2, y2)}"


def test_diagonal_arrow_moves_in_both_axes():
    ok, d = paint("↗")
    (x1, y1), (x2, y2) = endpoints(d.lines[0][0])
    assert x1 != x2 and y1 != y2, f"a diagonal is not axis-aligned: {d.lines[0]}"


def test_drawn_agrees_with_what_draw_cell_claims():
    for cp in glyphvec._ARROW_VECS:
        assert glyphvec.drawn(cp), f"U+{cp:04X} is vectorized"
    for ch in "─│┌█▀▄░⣿\ue0b0":
        assert glyphvec.drawn(ord(ch)), ch
    assert not glyphvec.drawn(ord("A"))
    assert not glyphvec.drawn(0x256D), "rounded corners are a font job, not geometry"
    assert not glyphvec.drawn(0x25A0), "BLACK SQUARE is inside the old range but a font job"
    assert not glyphvec.drawn(0x2504), "light horizontal dash-dash is a font job"


def test_unclaimed_cell_defers_to_the_font():
    """The range guard used to be a coarse `0x2500..0x259F`: inside it, anything
    unhandled fell through *after* claiming the cell — the picture lost the glyph
    silently. Now the claim is per codepoint."""
    for ch in ("■", "❴"):
        ok, d = paint(ch)
        assert ok is False and not d.lines and not d.rects, (ch, d.lines, d.rects)


def test_blocks_box_and_braille_still_paint():
    ok, d = paint("█")
    assert ok and d.rects, "full block"
    ok, d = paint("┌")
    assert ok and len(d.lines) == 2, f"┌ is two edges: {d.lines}"
    ok, d = paint("⣿")
    assert ok and len(d.ellipses) == 8, f"⣿ is eight dots: {d.ellipses}"
    ok, d = paint("⠀")
    assert ok and not d.ellipses, "⠀ must stay empty"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok {name}")
    print("test_glyphvec: all passed")
