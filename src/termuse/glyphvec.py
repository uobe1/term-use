"""Symbol ranges drawn as geometry, not glyphs.

Modern terminal UIs paint most of their chrome out of a handful of Unicode ranges
that a *single installed font* will not reliably cover — measured on this box,
DejaVu Sans Mono has box drawing and blocks but **no braille and no Powerline
PUA**, and nothing on a typical server has the PUA at all; on the device that
reported issue #3, Droid Sans Mono had no arrows either. The reference
implementations (asciinema's `agg`, kitty, foot) solve the same problem the same
way: they vectorize these ranges instead of hoping for a font. Same here, so a
starship prompt segment, a braille spinner, a `←` in a menu or a bordered panel
renders on a machine with no symbols font installed.

Each `draw_*` returns True when it painted the cell; the renderer falls back to
the font chain when it returns False.
"""

# Quadrant blocks: (upper-left, upper-right, lower-left, lower-right)
_QUADRANTS = {
    0x2596: (0, 0, 1, 0), 0x2597: (0, 0, 0, 1), 0x2598: (1, 0, 0, 0),
    0x2599: (1, 0, 1, 1), 0x259A: (1, 0, 0, 1), 0x259B: (1, 1, 1, 0),
    0x259C: (1, 1, 0, 1), 0x259D: (0, 1, 0, 0), 0x259E: (0, 1, 1, 0),
    0x259F: (0, 1, 1, 1),
}

# Light box drawing: which edges connect to the cell centre.
_BOX = {
    0x2500: (0, 0, 1, 1), 0x250C: (0, 1, 0, 1), 0x2510: (0, 1, 1, 0),
    0x2514: (1, 0, 0, 1), 0x2518: (1, 0, 1, 0), 0x251C: (1, 1, 0, 1),
    0x2524: (1, 1, 1, 0), 0x252C: (0, 1, 1, 1), 0x2534: (1, 0, 1, 1),
    0x253C: (1, 1, 1, 1), 0x2502: (1, 1, 0, 0),
}
_HEAVY_BOX = {0x2501: (0, 0, 1, 1), 0x2503: (1, 1, 0, 0), 0x250F: (0, 1, 0, 1),
              0x2513: (0, 1, 1, 0), 0x2517: (1, 0, 0, 1), 0x251B: (1, 0, 1, 0),
              0x2523: (1, 1, 0, 1), 0x252B: (1, 1, 1, 0), 0x2533: (0, 1, 1, 1),
              0x253B: (1, 0, 1, 1), 0x254B: (1, 1, 1, 1)}
_DOUBLE_BOX = {0x2550: (0, 0, 1, 1), 0x2551: (1, 1, 0, 0), 0x2554: (0, 1, 0, 1),
               0x2557: (0, 1, 1, 0), 0x255A: (1, 0, 0, 1), 0x255D: (1, 0, 1, 0),
               0x2560: (1, 1, 0, 1), 0x2563: (1, 1, 1, 0), 0x2566: (0, 1, 1, 1),
               0x2569: (1, 0, 1, 1), 0x256C: (1, 1, 1, 1)}

# Braille dot order in the codepoint bits, and where each dot sits in the cell
# (column, row) on a 2x4 grid.
_BRAILLE_DOTS = ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (0, 3), (1, 3))

# Arrows (U+2190–U+2199) as shaft directions. TUI menus, rewind lines and
# progress rails are built out of these, and the reporter's own device font
# (Droid Sans Mono) has no coverage for them: the pane drew `←` fine while the
# rendered picture showed tofu. One direction per arrow, two for the doubles.
_ARROW_VECS = {
    0x2190: ((-1, 0),), 0x2191: ((0, -1),), 0x2192: ((1, 0),), 0x2193: ((0, 1),),
    0x2194: ((-1, 0), (1, 0)), 0x2195: ((0, -1), (0, 1)),
    0x2196: ((-1, -1),), 0x2197: ((1, -1),), 0x2198: ((1, 1),), 0x2199: ((-1, 1),),
}


def _blend(fg, bg, frac: float):
    return tuple(int(round(b + (f - b) * frac)) for f, b in zip(fg, bg))


# Every codepoint `draw_cell` paints on its own, so the renderer knows which
# glyphs need no font and must not send it hunting a fallback face for them.
VECTORIZED = (frozenset(_ARROW_VECS) | frozenset(_QUADRANTS) | frozenset(_BOX)
              | frozenset(_HEAVY_BOX) | frozenset(_DOUBLE_BOX)
              | frozenset(range(0x2580, 0x2596)) | {0x254C, 0x254D}
              | frozenset(range(0xE0B0, 0xE0B4))
              | frozenset(range(0x2800, 0x2900)))


def drawn(cp: int) -> bool:
    """True for a codepoint painted geometrically — the font is never consulted."""
    return cp in VECTORIZED


def _draw_arrow(cp: int, x0: int, y0: int, w: int, h: int, fg, draw) -> bool:
    """Shaft plus barbs for one arrow codepoint, from the cell centre out."""
    vecs = _ARROW_VECS.get(cp)
    if not vecs:
        return False
    cx, cy = x0 + (w - 1) / 2, y0 + (h - 1) / 2
    t = max(1, min(w, h) // 8)
    barb = max(2, min(w // 4, h // 4))
    for dx, dy in vecs:
        tipx, tipy = cx + dx * (w // 2 - 1), cy + dy * (h // 2 - 1)
        # A double arrow (↔ ↕) draws two half-shafts from the centre; a single
        # one runs the shaft all the way to the opposite edge.
        tailx, taily = (cx, cy) if len(vecs) > 1 else (
            cx - dx * (w // 2 - 1), cy - dy * (h // 2 - 1))
        draw.line([tailx, taily, tipx, tipy], fill=fg, width=t)
        px, py = -dy, dx
        for s in (1, -1):
            draw.line([tipx, tipy,
                       tipx - dx * barb + s * px * barb,
                       tipy - dy * barb + s * py * barb], fill=fg, width=t)
    return True


def draw_cell(ch: str, x0: int, y0: int, w: int, h: int, fg, bg, draw) -> bool:
    """Paint one symbol cell geometrically. False = the font should try."""
    cp = ord(ch)
    if cp in _ARROW_VECS:
        return _draw_arrow(cp, x0, y0, w, h, fg, draw)
    if cp not in VECTORIZED:
        return False

    # --- blocks ---
    if cp == 0x2580:                       # ▀ upper half
        draw.rectangle([x0, y0, x0 + w - 1, y0 + h // 2 - 1], fill=fg)
        return True
    if cp == 0x2590:                        # ▐ right half
        draw.rectangle([x0 + w // 2, y0, x0 + w - 1, y0 + h - 1], fill=fg)
        return True
    if 0x2581 <= cp <= 0x2588:              # ▁..█ lower n/8 (2588 = full)
        n = cp - 0x2580 if cp < 0x2588 else 8
        top = y0 + h - max(1, h * n // 8)
        draw.rectangle([x0, top, x0 + w - 1, y0 + h - 1], fill=fg)
        return True
    if 0x2589 <= cp <= 0x258F:              # ▉..▏ left n/8
        n = 0x2590 - cp
        draw.rectangle([x0, y0, x0 + max(1, w * n // 8) - 1, y0 + h - 1], fill=fg)
        return True
    if cp == 0x2594:                        # ▔ upper 1/8
        draw.rectangle([x0, y0, x0 + w - 1, y0 + max(1, h // 8) - 1], fill=fg)
        return True
    if cp == 0x2595:                        # ▕ right 1/8
        x = x0 + w - max(1, w // 8)
        draw.rectangle([x, y0, x0 + w - 1, y0 + h - 1], fill=fg)
        return True
    if 0x2591 <= cp <= 0x2593:              # ░ ▒ ▓ shades
        frac = {0x2591: 0.25, 0x2592: 0.5, 0x2593: 0.75}[cp]
        draw.rectangle([x0, y0, x0 + w - 1, y0 + h - 1], fill=_blend(fg, bg, frac))
        return True
    if cp in _QUADRANTS:
        ul, ur, ll, lr = _QUADRANTS[cp]
        mx, my = x0 + w // 2, y0 + h // 2
        if ul:
            draw.rectangle([x0, y0, mx - 1, my - 1], fill=fg)
        if ur:
            draw.rectangle([mx, y0, x0 + w - 1, my - 1], fill=fg)
        if ll:
            draw.rectangle([x0, my, mx - 1, y0 + h - 1], fill=fg)
        if lr:
            draw.rectangle([mx, my, x0 + w - 1, y0 + h - 1], fill=fg)
        return True

    # --- box drawing: edges from the centre, single/heavy/double ---
    spec = _BOX.get(cp) or _HEAVY_BOX.get(cp) or _DOUBLE_BOX.get(cp)
    if spec:
        top, bottom, left, right = spec
        double = cp in _DOUBLE_BOX
        heavy = cp in _HEAVY_BOX
        t = max(1, w // 6) if heavy else max(1, min(2, w // 10))
        cx, cy = x0 + w // 2, y0 + h // 2
        gap = max(1, h // 8) if double else 0

        def line(x1, y1, x2, y2):
            draw.line([x1, y1, x2, y2], fill=fg, width=t)
            if double:
                if y1 == y2:                     # horizontal pair
                    draw.line([x1, y1 - gap, x2, y2 - gap], fill=fg, width=t)
                else:
                    draw.line([x1 - gap, y1, x2 - gap, y2], fill=fg, width=t)

        if left:
            line(x0, cy, cx, cy)
        if right:
            line(cx, cy, x0 + w - 1, cy)
        if top:
            line(cx, y0, cx, cy)
        if bottom:
            line(cx, cy, cx, y0 + h - 1)
        if not (left or right or top or bottom):
            return False
        return True
    if cp in (0x254C, 0x254D):               # ╌╍ dashes
        seg = max(1, w // 4)
        y = y0 + h // 2
        for sx in (x0, x0 + 2 * seg):
            draw.rectangle([sx, y, min(sx + seg - 1, x0 + w - 1), y + 1], fill=fg)
        return True

    # --- powerline separators (the PUA shapes prompts are made of) ---
    if cp in (0xE0B0, 0xE0B1, 0xE0B2, 0xE0B3):
        mid = y0 + h // 2
        if cp in (0xE0B0, 0xE0B1):           # pointing right
            draw.polygon([(x0, y0), (x0, y0 + h - 1), (x0 + w - 1, mid)], fill=fg)
        else:                                 # pointing left
            draw.polygon([(x0 + w - 1, y0), (x0 + w - 1, y0 + h - 1), (x0, mid)],
                         fill=fg)
        return True

    # --- braille (spinners, sparklines, tiny charts) ---
    if 0x2800 <= cp <= 0x28FF:
        bits = cp - 0x2800
        r = max(1, min(w, h) // 8)
        colw, rowh = w / 2.0, h / 4.0
        for i, (c, row) in enumerate(_BRAILLE_DOTS):
            if not (bits >> i) & 1:
                continue
            px = int(x0 + c * colw + colw / 2)
            py = int(y0 + row * rowh + rowh / 2)
            draw.ellipse([px - r, py - r, px + r, py + r], fill=fg)
        return True
    return False
