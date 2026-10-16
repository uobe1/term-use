"""Text observation: SGR-aware cell grid, attribute runs, attribute grep.

Input is `tmux capture-pane -e -p` output (re-synthesized escape sequences).
The SGR state machine treats the stream as characters: `\n` moves rows, SGR
state carries across lines until explicitly reset. fg/bg come out as color
names for 0-15, ints for 256-palette, #rrggbb for truecolor.

Column truth: tmux reserves *two* columns for a double-width glyph but emits
only the glyph (the padding cell is skipped by grid_string_cells), so a
character-indexed reader drifts one column per wide char — which silently
mis-aims `--find` coordinates at `click`. Every cell therefore carries `w`
(1 or 2) and the reserved second half appears as a `pad` stub.
"""
import re
import unicodedata

NAMES = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")

CSI = re.compile(r"\x1b\[([0-9;:]*)([A-Za-z])")

# Ranges terminals treat as emoji-width-2 regardless of their East Asian
# Width property (tmux keeps a hand-tuned override list for the same reason).
_WIDE_EMOJI = ((0x1F300, 0x1F9FF), (0x1FA70, 0x1FAFF), (0x1F1E6, 0x1F1FF))


def cell_width(ch: str) -> int:
    """Columns this character reserves on screen — an approximation of the
    pane's own measurement, exact for the case everyone actually hits (CJK).

    Ambiguous-width characters (±, °, box drawing) count as 1, matching tmux's
    default; a hand-tuned emoji list matches the 2-cell behavior. Where the
    guess and the terminal disagree the *screen* stays authoritative: this is
    why the grid also reports `w` per cell instead of making readers recompute.
    """
    o = ord(ch)
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    if any(lo <= o <= hi for lo, hi in _WIDE_EMOJI):
        return 2
    return 1

# Everything an escape-sequence-aware reader would not print as a glyph:
# OSC (BEL- or ST-terminated), CSI (incl. private `?` modes), designations,
# and any other two-char escape. Used to size a capture honestly (escape
# bytes are not context an agent pays for) and to regex the raw stream.
_ESCAPES = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"      # OSC .. BEL | ST
    r"|\x1b\[[0-9;:]*[0-9;:?]*[A-Za-z~]"      # CSI ... final byte
    r"|\x1b[()][0-9A-Za-z]"                    # charset designation
    r"|\x1b[@-Z\\-_]"                          # other two-char escapes
)


def visible_text(text: str) -> str:
    """Strip escape sequences and CR, keep line structure."""
    return _ESCAPES.sub("", text).replace("\r", "")


def color_name(n: int) -> str | int:
    if 0 <= n <= 7:
        return NAMES[n]
    if 8 <= n <= 15:
        return "bright-" + NAMES[n - 8]
    return n  # 256-palette index


def truecolor(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _apply_sgr(params: str, st: dict) -> None:
    nums = [int(p) for p in params.replace(":", ";").split(";") if p] or [0]
    k = 0
    while k < len(nums):
        n = nums[k]
        if n == 0:
            st.update(fg=None, bg=None, attrs=set())
        elif n == 1:
            st["attrs"].add("bold")
        elif n == 2:
            st["attrs"].add("dim")
        elif n == 3:
            st["attrs"].add("italic")
        elif n == 4:
            st["attrs"].add("underline")
        elif n == 5:
            st["attrs"].add("blink")
        elif n == 7:
            st["attrs"].add("reverse")
        elif n == 9:
            st["attrs"].add("strikethrough")
        elif n == 22:
            st["attrs"].difference_update(("bold", "dim"))
        elif n == 23:
            st["attrs"].discard("italic")
        elif n == 24:
            st["attrs"].discard("underline")
        elif n == 27:
            st["attrs"].discard("reverse")
        elif n == 29:
            st["attrs"].discard("strikethrough")
        elif 30 <= n <= 37:
            st["fg"] = color_name(n - 30)
        elif n == 38:
            if k + 1 < len(nums) and nums[k + 1] == 5:
                st["fg"] = nums[k + 2] if k + 2 < len(nums) else None
                k += 2
            elif k + 1 < len(nums) and nums[k + 1] == 2:
                if k + 4 < len(nums):
                    st["fg"] = truecolor(tuple(nums[k + 2:k + 5]))
                k += 4
        elif n == 39:
            st["fg"] = None
        elif 40 <= n <= 47:
            st["bg"] = color_name(n - 40)
        elif n == 48:
            if k + 1 < len(nums) and nums[k + 1] == 5:
                st["bg"] = nums[k + 2] if k + 2 < len(nums) else None
                k += 2
            elif k + 1 < len(nums) and nums[k + 1] == 2:
                if k + 4 < len(nums):
                    st["bg"] = truecolor(tuple(nums[k + 2:k + 5]))
                k += 4
        elif n == 49:
            st["bg"] = None
        elif 90 <= n <= 97:
            st["fg"] = color_name(n - 90 + 8)
        elif 100 <= n <= 107:
            st["bg"] = color_name(n - 100 + 8)
        k += 1


def parse_grid(text: str) -> list[list[dict | None]]:
    """SGR byte stream -> sparse cell grid; None marks untouched slots."""
    st = {"fg": None, "bg": None, "attrs": set()}
    grid: list[list[dict | None]] = [[]]
    row = col = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\x1b":
            m = CSI.match(text, i)
            if m:
                if m.group(2) == "m":
                    _apply_sgr(m.group(1), st)
                i = m.end()
            else:
                i += 1  # lone ESC or non-CSI escape: ignore
            continue
        if ch == "\n":
            row += 1
            col = 0
            while row >= len(grid):
                grid.append([])
            i += 1
            continue
        if ch == "\r":
            col = 0
            i += 1
            continue
        line = grid[row]
        w = cell_width(ch)
        if w == 0 and col > 0:
            while len(line) < col:
                line.append(None)
            prev = line[col - 1]
            if prev is not None and not prev.get("pad"):
                prev["char"] += ch  # combining mark joins the cluster before it
                i += 1
                continue
            w = 1  # orphan mark at line start: let it hold a column
        while len(line) <= col + w - 1:
            line.append(None)
        line[col] = {"char": ch, "fg": st["fg"], "bg": st["bg"],
                     "attrs": sorted(st["attrs"]), "x": col, "y": row, "w": w}
        for p in range(col + 1, col + w):
            # tmux reserves this column for the glyph and emits nothing for
            # it; keeping an explicit stub means column arithmetic downstream
            # (clicks, the renderer's pitch) cannot drift past wide chars.
            line[p] = {"char": "", "pad": True, "fg": st["fg"], "bg": st["bg"],
                       "attrs": sorted(st["attrs"]), "x": p, "y": row, "w": 0}
        col += w
        i += 1
    # capture-pane terminates the last screen row with "\n", which the loop
    # above reads as "start another row": without this, every grid carries a
    # phantom empty row past the pane's height (and every image is one row
    # taller than the terminal it claims to show).
    if len(grid) > 1 and not grid[-1] and text.endswith("\n"):
        grid.pop()
    return grid


def row_glyphs(row: list[dict | None]):
    """(column, cell) for the cells that carry something a reader sees.

    Padding stubs are skipped: their glyph lives in the cluster to the left,
    so a text view that kept them would insert a space inside one character.
    """
    for x, c in enumerate(row):
        if c is not None and c.get("pad"):
            continue
        yield x, c


def rows_to_runs(grid: list[list[dict | None]]) -> list[list[dict]]:
    """Merge adjacent same-attribute cells (None = plain space) into runs."""
    out = []
    for row in grid:
        runs: list[dict] = []
        for _x, cell in row_glyphs(row):
            if cell is None:
                ch, fg, bg, attrs = " ", None, None, []
            else:
                ch, fg, bg, attrs = cell["char"], cell["fg"], cell["bg"], cell["attrs"]
            if runs and runs[-1]["fg"] == fg and runs[-1]["bg"] == bg \
                    and runs[-1]["attrs"] == attrs:
                runs[-1]["text"] += ch
            else:
                runs.append({"text": ch, "fg": fg, "bg": bg, "attrs": list(attrs)})
        out.append(runs)
    return out


def grid_row_text(row: list[dict | None]) -> str:
    return "".join((c["char"] if c else " ") for _x, c in row_glyphs(row)).rstrip()


def row_matches(row: list[dict | None], fg=None, bg=None, attrs=()) -> bool:
    return any(
        c is not None
        and (fg is None or c["fg"] == fg)
        and (bg is None or c["bg"] == bg)
        and all(a in c["attrs"] for a in attrs)
        for c in row
    )


def cell_at(grid: list[list[dict | None]], x: int, y: int) -> dict:
    """Screen-coordinate cell lookup; untouched slots look like plain spaces.

    Asking for the reserved half of a wide glyph answers with the glyph that
    owns it (that is what a human clicking there means), plus which column was
    asked for.
    """
    blank = {"char": " ", "fg": None, "bg": None, "attrs": [], "x": x, "y": y, "w": 1}
    if not (0 <= y < len(grid)) or not (0 <= x < len(grid[y])):
        return blank
    c = grid[y][x]
    if c is None:
        return blank
    if c.get("pad"):
        owner = next((cc for xx, cc in reversed(list(enumerate(grid[y][:x])))
                      if cc is not None and not cc.get("pad")), None)
        if owner:
            return {**owner, "column_asked": x}
    return c


def find_text(grid: list[list[dict | None]], needle: str) -> list[dict]:
    """All literal matches on the visible screen, in reading order.

    Coordinates are 1-based and click-ready (same convention as `click`),
    so the locate-then-click flow is a single hop instead of hand-counting
    columns in screen output. Matches stay within one visual row (wrapped
    lines are separate rows — the agent clicks what it sees). Columns are
    counted the way the pane counts them, so a match after a double-width
    glyph reports the screen column, not the character offset.
    """
    if not needle:
        return []
    out: list[dict] = []
    for y, row in enumerate(grid):
        pairs = list(row_glyphs(row))
        line = "".join((c["char"] if c else " ") for _x, c in pairs)
        start = 0
        while (idx := line.find(needle, start)) != -1:
            col = pairs[idx][0] if idx < len(pairs) else len(line)
            out.append({"text": needle, "x": col + 1, "y": y + 1})
            start = idx + 1
    return out


def grid_stats(grid: list[list[dict | None]]) -> dict:
    """What the screen actually holds — so "empty" is never confused with
    "I failed to read it".

    A pane is blank when nothing is *visible*: no non-space glyph and no
    colored or attributed cell. A freshly `clear`ed screen, a daemon that
    prints nothing, and a full-screen program that exited all look like this,
    and all are normal states an observer must be able to name.
    """
    painted = 0
    glyphs = 0
    styled = 0
    nonblank_rows = 0
    for row in grid:
        row_visible = False
        for _x, c in row_glyphs(row):
            if c is None:
                continue
            painted += 1
            if c["char"] != " ":
                glyphs += 1
                row_visible = True
            elif c["bg"] is not None or c["attrs"]:
                styled += 1
                row_visible = True
        if row_visible:
            nonblank_rows += 1
    return {"painted_cells": painted, "glyph_cells": glyphs, "styled_cells": styled,
            "nonblank_rows": nonblank_rows,
            "blank": glyphs == 0 and styled == 0}


def text_stats(text: str) -> dict:
    """Size of a text capture, in the units an agent's context is charged in."""
    lines = text.splitlines()
    return {"lines": len(lines),
            "nonblank_lines": sum(1 for ln in lines if ln.strip()),
            "bytes": len(text.encode("utf-8"))}
