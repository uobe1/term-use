"""Image channel: render the SGR cell grid to PNG/JPG via Pillow.

The terminal is the ground truth (capture-pane -e text), so the picture is
an exact re-synthesis: per-cell foreground/background from the grid, reverse
swapped, bold lifting 0-7 colors to their bright variants, xterm 256-palette
mapping for numeric colors, truecolor passed through.
"""
import glob
import os

from .errors import TDError
from . import screen


# xterm-style base 16 palette (0-15)
BASE16 = (
    (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
    (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229),
    (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
)
NAMES16 = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")

FG_DEFAULT = (229, 229, 229)
BG_DEFAULT = (0, 0, 0)

FONT_SIZE = 16

# Preferred monospace families, best first. Ranking exists because glob order
# is arbitrary: a box with several monos must not settle for whichever the
# filesystem happened to list first.
MONO_FAMILIES = (
    "dejavusansmono", "liberationmono", "freesansmono", "notosansmono",
    "droidsansmono", "ubuntumono", "cascadiamono", "jetbrainsmono", "hack",
)

# Standard freedesktop font locations, probed only after the Android/Termux
# ones. Without these the image channel was dead on every desktop/server Linux.
LINUX_FONT_DIRS = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.join(os.environ.get("XDG_DATA_HOME")
                 or os.path.expanduser("~/.local/share"), "fonts"),
    os.path.expanduser("~/.fonts"),
)


def rank_mono(name: str) -> int:
    """Lower is better; unknown families share the tail and sort by name."""
    low = name.lower()
    for i, family in enumerate(MONO_FAMILIES):
        if family in low:
            return i
    return len(MONO_FAMILIES)


STYLE_TOKENS = ("bold", "black", "extralight", "light", "medium", "semibold",
                "italic", "oblique", "condensed", "narrow")


def rank_style(name: str) -> int:
    """Base weight first: the grid already carries bold as an attribute.

    Picking a Bold/Oblique cut would thicken *every* cell, so an unstyled face
    outranks a styled one, and an explicit Regular/Roman beats a bare filename.
    """
    low = name.lower()
    if "regular" in low or "roman" in low:
        return 0
    if any(tok in low for tok in STYLE_TOKENS):
        return 2
    return 1


def select_mono(dirs) -> str | None:
    """Best-ranked monospace .ttf/.otf under dirs, or None if there is none.

    Proportional fonts are refused on purpose: they redraw the cell grid at the
    wrong pitch, so the picture would silently misrepresent the screen.
    """
    cands = []
    for d in dirs:
        for pattern in ("**/*.ttf", "**/*.otf"):
            for path in glob.glob(os.path.join(d, pattern), recursive=True):
                if "mono" in os.path.basename(path).lower():
                    cands.append(path)
    if not cands:
        return None
    return sorted(cands, key=lambda p: (
        rank_mono(os.path.basename(p)),
        rank_style(os.path.basename(p)),
        os.path.basename(p)))[0]


def find_font() -> str | None:
    """Probing order: $TERM_USE_FONT, DroidSansMono, any /system mono,
    $PREFIX share mono, then the standard Linux font dirs."""
    override = os.environ.get("TERM_USE_FONT")
    if override:
        if not os.path.isfile(override):
            raise TDError(
                "font-missing",
                f"TERM_USE_FONT points at {override!r}, which is not a file",
                hint="unset TERM_USE_FONT to auto-probe, or point it at a real "
                     "monospace .ttf/.otf")
        return override
    cands = glob.glob("/system/fonts/DroidSansMono*.ttf")
    cands += [p for p in glob.glob("/system/fonts/*.ttf") if "mono" in p.lower()]
    prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
    cands += [p for p in glob.glob(f"{prefix}/share/fonts/**/*.ttf", recursive=True)
              if "mono" in p.lower()]
    if cands:
        return cands[0]
    return select_mono(LINUX_FONT_DIRS)


def pixel(color) -> tuple | None:
    """Color value (name | int | #hex) -> RGB tuple; None stays None."""
    if color is None:
        return None
    if isinstance(color, int):
        return _palette256(color)
    if color.startswith("#") and len(color) == 7:
        return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    bright = False
    name = color
    if color.startswith("bright-"):
        bright, name = True, color[len("bright-"):]
    if name in NAMES16:
        return BASE16[NAMES16.index(name) + (8 if bright else 0)]
    return None


def _palette256(n: int) -> tuple:
    if n < 16:
        return BASE16[n]
    if n < 232:  # 6x6x6 color cube
        n -= 16
        lv = (0, 95, 135, 175, 215, 255)
        return (lv[n // 36], lv[(n // 6) % 6], lv[n % 6])
    g = 8 + (n - 232) * 10  # grayscale ramp
    return (g, g, g)


def render(grid: list, out_path: str, fmt: str, cols: int | None = None,
           rows: int | None = None) -> dict:
    """Draw the grid; returns size/font info.

    `cols`/`rows` are the pane's real geometry: the picture must be shaped
    like the terminal window, not cropped to wherever the last visible glyph
    sits — a cropped frame misrepresents layout (which is the whole reason an
    agent asks for an image) and, on a screen that painted nothing at all,
    produces a zero-area canvas Pillow refuses to save. The 1-cell floor is
    the crash guard; callers with a live pane always pass the geometry.

    Raises ImportError without Pillow, TDError("font-missing") without a font.
    """
    from PIL import Image, ImageDraw, ImageFont

    stats = screen.grid_stats(grid)
    font_path = find_font()
    if font_path is None:
        raise TDError(
            "font-missing",
            "no monospace font found in the probed locations",
            hint="install one (apt-get install fonts-dejavu-core) or set "
                 "TERM_USE_FONT=/path/to/Mono.ttf; the text channels need no font")
    font = ImageFont.truetype(font_path, FONT_SIZE)
    charw = max(1, round(font.getlength("M")))
    ascent, descent = font.getmetrics()
    lineh = ascent + descent
    height = int(rows) if rows and int(rows) > 0 else max(len(grid), 1)
    width = int(cols) if cols and int(cols) > 0 else max(
        (len(r) for r in grid), default=0) or 1
    img = Image.new("RGB", (width * charw, height * lineh), BG_DEFAULT)
    draw = ImageDraw.Draw(img)
    for row in grid:
        for cell in row:
            if cell is None or cell.get("pad"):
                continue
            fg = pixel(cell["fg"]) or FG_DEFAULT
            bg = pixel(cell["bg"]) or BG_DEFAULT
            attrs = cell.get("attrs") or []
            if "reverse" in attrs:
                fg, bg = bg, fg
            elif "bold" in attrs and isinstance(cell["fg"], str) \
                    and cell["fg"] in NAMES16:
                fg = BASE16[NAMES16.index(cell["fg"]) + 8]
            # A double-width glyph owns two columns: the background must span
            # both and the next cell must start after both, or the whole frame
            # shifts left of where the terminal actually put it.
            w = cell.get("w", 1) or 1
            x0, y0 = cell["x"] * charw, cell["y"] * lineh
            draw.rectangle([x0, y0, x0 + w * charw - 1, y0 + lineh - 1], fill=bg)
            draw.text((x0, y0), cell["char"], font=font, fill=fg)
    if fmt == "jpg":
        img.save(out_path, "JPEG", quality=92)
    else:
        img.save(out_path, "PNG")
    return {"path": out_path, "size": [width * charw, height * lineh],
            "grid": [width, height], "charw": charw, "lineh": lineh,
            "font": os.path.basename(font_path),
            "blank": stats["blank"], "nonblank_rows": stats["nonblank_rows"],
            "cells": stats["painted_cells"]}
