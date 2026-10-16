"""Image channel: render the SGR cell grid to PNG/JPG via Pillow.

The terminal is the ground truth (capture-pane -e text), so the picture is
an exact re-synthesis: per-cell foreground/background from the grid, reverse
swapped, bold lifting 0-7 colors to their bright variants, xterm 256-palette
mapping for numeric colors, truecolor passed through.
"""
import glob
import os

from .errors import TDError
from . import screen, glyphvec, ttffont


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

# Standard locations, probed in a *convention* order: an environment variable
# the operator set means something, so env-derived paths come before
# machine-wide ones. Nothing here names a device — `$PREFIX` is the shared
# convention of conda, Nix profiles, stow and Termux alike.
def font_dirs() -> list[str]:
    """Directories to search for monospace fonts.

    `TERM_USE_FONT_DIRS` (path-separated) *replaces* the list: it is a
    statement of what to use, not a hint, so pointing it at an empty directory
    must yield font-missing rather than silently falling back elsewhere.
    """
    override = os.environ.get("TERM_USE_FONT_DIRS")
    if override:
        return [p for p in override.split(os.pathsep) if p]
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
        "~/.local/share")
    dirs = [data_home + "/fonts"]
    for prefix in filter(None, (os.environ.get("PREFIX"),
                                os.environ.get("SYSROOT"),
                                os.environ.get("CONDA_PREFIX"),
                                os.environ.get("NM_PREFIX_PATH"))):
        dirs.append(os.path.join(prefix, "share", "fonts"))
    dirs += [
        os.path.expanduser("~/.fonts"),
        os.path.expanduser("~/.local/share/fonts"),
        "/usr/local/share/fonts",
        "/usr/share/fonts",
        "/usr/share/fonts/truetype",
        # macOS conventions
        os.path.expanduser("~/Library/Fonts"),
        "/Library/Fonts",
        "/System/Library/Fonts",
        # Android system font dir (kept as one convention among many, no
        # longer first — it used to be the only thing probed)
        "/system/fonts",
    ]
    return dirs


def _candidate_paths(dirs) -> list[str]:
    out = []
    for d in dirs:
        for pattern in ("**/*.ttf", "**/*.otf", "**/*.ttc"):
            out.extend(glob.glob(os.path.join(d, pattern), recursive=True))
    return out


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

    Proportional faces are refused on purpose: they redraw the cell grid at the
    wrong pitch, so the picture would silently misrepresent the screen.
    """
    cands = [p for p in _candidate_paths(dirs) if is_mono_name(p)]
    if not cands:
        return None
    return sorted(cands, key=lambda p: (
        rank_mono(os.path.basename(p)),
        rank_style(os.path.basename(p)),
        os.path.basename(p)))[0]


# Names that mean "fixed pitch" in a filename, plus the well-known monospace
# families that do not carry the word (Menlo, Consolas, Courier, SF Mono...).
MONO_NAME_HINTS = ("mono", "menlo", "consolas", "courier", "cascadia", "hack",
                   "jetbrains", "fira", "source-code", "sourcecode", "inconsolata",
                   "input", "ibm-plex-mono", "plexmono", "ubuntu", "agave",
                   "iosevka", "cotrone", "comic-mono", "recursive-mono")


def is_mono_name(path: str) -> bool:
    low = os.path.basename(path).lower().replace("_", "-")
    return any(h in low for h in MONO_NAME_HINTS)


def find_font() -> str | None:
    """$TERM_USE_FONT (a file) wins; otherwise probe by convention."""
    override = os.environ.get("TERM_USE_FONT")
    if override:
        if not os.path.isfile(override):
            raise TDError(
                "font-missing",
                f"TERM_USE_FONT points at {override!r}, which is not a file",
                hint="unset TERM_USE_FONT to auto-probe, or point it at a real "
                     "monospace .ttf/.otf")
        return override
    return select_mono(font_dirs())


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


def _parse_color(value, default):
    if value and value.startswith("#") and len(value) == 7:
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    return default


def theme() -> tuple[tuple, tuple]:
    """Terminal default colors: the pane's own defaults, not ours.

    A light-background terminal rendered with our hardcoded dark defaults is a
    picture of a screen that does not exist.
    """
    return (_parse_color(os.environ.get("TERM_USE_FG"), FG_DEFAULT),
            _parse_color(os.environ.get("TERM_USE_BG"), BG_DEFAULT))


# Faces worth trying after the primary, for the coverage a plain UI mono lacks:
# symbol packs (Nerd Fonts), wide-coverage fallbacks (Unifont), CJK, emoji.
_FALLBACK_HINTS = ("nerd", "symbols", "unifont", "noto", "cjk", "source-han",
                   "wqy", "wenquanyi", "emoji", "symbola", "free")


def fallback_fonts(primary: str, limit: int = 4) -> list[str]:
    """Additional faces to consult per character, best coverage first."""
    seen, out = {os.path.realpath(primary)}, []
    for p in _candidate_paths(font_dirs()):
        low = os.path.basename(p).lower()
        if not any(h in low for h in _FALLBACK_HINTS):
            continue
        real = os.path.realpath(p)
        if real in seen:
            continue
        seen.add(real)
        # symbol packs and Unifont exist to cover what UI fonts lack
        score = 0 if ("nerd" in low or "symbols" in low or "unifont" in low) else 1
        out.append((score, low, p))
    return [p for _s, _l, p in sorted(out, key=lambda t: (t[0], t[1]))][:limit]


class _Chain:
    """Primary + fallback faces, each with its loaded font and cmap reader."""

    def __init__(self, primary: str, size: int, ImageFont):
        self.entries = []
        for path in [primary, *fallback_fonts(primary)]:
            try:
                font = ImageFont.truetype(path, size)
            except OSError:
                continue                      # unreadable face: skip it honestly
            self.entries.append((path, font, ttffont.load(path)))
        if not self.entries:
            raise TDError(
                "font-missing",
                f"{primary!r} could not be loaded as a font",
                hint="install a monospace font (apt-get install "
                     "fonts-dejavu-core) or point TERM_USE_FONT at a real one")
        self.primary = self.entries[0][0]
        self.face = self.entries[0][2]
        self.charw = max(1, round(self.entries[0][1].getlength("/")))
        ascent, descent = self.entries[0][1].getmetrics()
        self.lineh = ascent + descent
        self.ascent = ascent
        upos, usthk = self.face.underline or (-100, 50)
        upem = self.face.units_per_em or 1000
        self.underline_y = ascent + max(1, round(-upos * size / upem))
        self.underline_t = max(1, round(usthk * size / upem))

    def pick(self, ch: str):
        """(font, path, is_primary) for the first face that has the glyph.

        `covers()` returning None means the face's cmap could not be read, so
        the face is trusted rather than skipped — refusing there would drop
        whole screens to a parser limitation.
        """
        for i, (path, font, face) in enumerate(self.entries):
            c = face.covers(ch[0]) if ch else None
            if c is None or c:
                return font, path, i == 0
        return None, None, False


def render(grid: list, out_path: str, fmt: str, cols: int | None = None,
           rows: int | None = None, cursor: tuple[int, int] | None = None,
           font_size: int = FONT_SIZE) -> dict:
    """Draw the grid; returns size/font/glyph accounting.

    `cols`/`rows` are the pane's real geometry: the picture must be shaped
    like the terminal window, not cropped to wherever the last visible glyph
    sits — a cropped frame misrepresents layout (which is the whole reason an
    agent asks for an image) and, on a screen that painted nothing at all,
    produced a zero-area canvas Pillow refuses to save. The 1-cell floor is
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
                 "TERM_USE_FONT=/path/to/Mono.ttf / TERM_USE_FONT_DIRS=/dir "
                 "with one in it; the text channels need no font")
    default_fg, default_bg = theme()
    chain = _Chain(font_path, font_size, ImageFont)
    charw, lineh = chain.charw, chain.lineh
    height = int(rows) if rows and int(rows) > 0 else max(len(grid), 1)
    width = int(cols) if cols and int(cols) > 0 else max(
        (len(r) for r in grid), default=0) or 1
    img = Image.new("RGB", (width * charw, height * lineh), default_bg)
    draw = ImageDraw.Draw(img)

    glyphs = {"cells": 0, "vector": 0, "primary": 0, "fallback": 0, "missing": 0}
    used_fallbacks: set[str] = set()
    missing: dict[str, int] = {}

    for row in grid:
        for cell in row:
            if cell is None or cell.get("pad"):
                continue
            attrs = cell.get("attrs") or []
            fg = pixel(cell["fg"]) or default_fg
            bg = pixel(cell["bg"]) or default_bg
            if "reverse" in attrs:
                fg, bg = bg, fg
            elif "bold" in attrs and isinstance(cell["fg"], str) \
                    and cell["fg"] in NAMES16:
                fg = BASE16[NAMES16.index(cell["fg"]) + 8]
            if "dim" in attrs:
                fg = tuple(int(round(b + (f - b) * 0.5)) for f, b in zip(fg, bg))
            # A double-width glyph owns two columns: the background must span
            # both and the next cell must start after both, or the whole frame
            # shifts left of where the terminal actually put it.
            w = (cell.get("w", 1) or 1) * charw
            x0, y0 = cell["x"] * charw, cell["y"] * lineh
            if bg != default_bg:
                draw.rectangle([x0, y0, x0 + w - 1, y0 + lineh - 1], fill=bg)
            ch = cell["char"]
            if ch == " ":
                continue
            glyphs["cells"] += 1
            if glyphvec.draw_cell(ch, x0, y0, w, lineh, fg, bg, draw):
                glyphs["vector"] += 1
            else:
                font, used_path, is_primary = chain.pick(ch)
                if font is None:
                    # No face on this machine can draw it. Say so, and draw a
                    # deliberate missing box rather than a silent tofu guess.
                    glyphs["missing"] += 1
                    key = f"U+{ord(ch[0]):04X}"
                    missing[key] = missing.get(key, 0) + 1
                    draw.rectangle([x0 + 1, y0 + 1, x0 + w - 2, y0 + lineh - 2],
                                   outline=fg)
                else:
                    draw.text((x0, y0), ch, font=font, fill=fg)
                    if is_primary:
                        glyphs["primary"] += 1
                    else:
                        glyphs["fallback"] += 1
                        used_fallbacks.add(os.path.basename(used_path))
            if "underline" in attrs:
                uy = y0 + chain.underline_y
                draw.rectangle([x0, uy, x0 + w - 1, uy + chain.underline_t - 1],
                               fill=fg)
            if "strikethrough" in attrs:
                my = y0 + chain.ascent // 2
                draw.rectangle([x0, my, x0 + w - 1, my + chain.underline_t - 1],
                               fill=fg)

    if cursor is not None:
        cx, cy = cursor
        if 0 <= cx < width and 0 <= cy < height:
            # Outline, not a filled block: the glyph under the cursor is the
            # thing the agent needs to read.
            border = default_fg if default_fg != default_bg else (255, 255, 255)
            draw.rectangle([cx * charw, cy * lineh,
                            (cx + 1) * charw - 1, (cy + 1) * lineh - 1],
                           outline=border, width=1)

    palette = False
    if fmt == "jpg":
        img.save(out_path, "JPEG", quality=92)
    else:
        # A terminal screen uses far fewer than 256 colors, so an exact-count
        # palette is lossless and roughly 7x smaller for the same pixels.
        colors = img.getcolors(maxcolors=256)
        if colors:
            img = img.quantize(colors=len(colors), method=Image.Quantize.MEDIANCUT,
                               dither=Image.Dither.NONE)
            palette = True
        img.save(out_path, "PNG")

    if glyphs["missing"]:
        glyphs["missing_samples"] = [
            {"codepoint": k, "count": v}
            for k, v in sorted(missing.items(), key=lambda kv: -kv[1])[:8]]
    else:
        glyphs["missing_samples"] = []
    glyphs["fallback_fonts"] = sorted(used_fallbacks)
    return {"path": out_path, "size": [width * charw, height * lineh],
            "grid": [width, height], "charw": charw, "lineh": lineh,
            "font": os.path.basename(chain.primary), "palette": palette,
            "blank": stats["blank"], "nonblank_rows": stats["nonblank_rows"],
            "cells": stats["painted_cells"], "glyphs": glyphs}
