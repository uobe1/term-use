"""Minimal sfnt (TTF/OTF/TTC) table reader: what a face can actually draw.

The image channel must not paint a glyph it does not have. Pillow rasterizes
whatever it is handed and produces a .notdef box for a missing codepoint — and
it computes `FT_Get_Char_Index` internally then throws the result away, so
"does this font cover U+E0B0" cannot be asked of Pillow. Answering it from the
font's own `cmap` is a few dozen lines of `struct` unpacking, and it keeps the
promise the whole tool runs on: report what is true instead of approximating.

Used for two things:
  * per-cell font fallback (a Nerd Font icon, a braille spinner, a CJK label —
    resolved against the faces that are actually installed), and
  * honest `missing` counts when nothing on the machine can draw it.

Anything this parser cannot read (WOFF, a broken table, a cmap format we do not
implement) yields coverage `None` = *unknown*, and the renderer then trusts the
font rather than refusing to draw it: guessing wrong that way costs one tofu
box, while refusing would cost the whole picture.
"""
import struct

BMP = 0xFFFF


class Face:
    """One font file, read far enough to answer coverage questions."""

    def __init__(self, path: str):
        self.path = path
        self.error: str | None = None
        self.fixed_pitch: bool | None = None
        self.underline: tuple[int, int] | None = None   # (position, thickness)
        self.units_per_em: int | None = None
        self._segments: list[tuple[int, int, int, int]] | None = None  # fmt 4
        self._ro_base: int = 0
        self._groups: list[tuple[int, int, int]] | None = None         # fmt 12/13
        self._unknown = True
        self._data = b""
        try:
            with open(path, "rb") as fh:
                self._data = fh.read()
            self._parse()
        except Exception as exc:  # a strange font must not kill the channel
            self.error = f"{type(exc).__name__}: {exc}"
            self._segments = self._groups = None
            self._unknown = True

    # -- table directory ---------------------------------------------------
    def _tables(self) -> dict[str, tuple[int, int]]:
        data = self._data
        if len(data) < 12:
            raise ValueError("too small for an sfnt header")
        base = 0
        tag = data[:4]
        if tag == b"ttcf":                 # TrueType Collection: read the first face
            if struct.unpack_from(">I", data, 8)[0] < 1:
                raise ValueError("empty ttcf")
            base = struct.unpack_from(">I", data, 12)[0]
            tag = data[base:base + 4]
        if tag not in (b"\x00\x01\x00\x00", b"true", b"typ1", b"OTTO"):
            raise ValueError(f"not sfnt: {tag!r}")
        num = struct.unpack_from(">H", data, base + 4)[0]
        out: dict[str, tuple[int, int]] = {}
        for i in range(num):
            rec = base + 12 + 16 * i
            out[data[rec:rec + 4].decode("latin-1")] = struct.unpack_from(
                ">II", data, rec + 8)
        return out

    def _parse(self) -> None:
        tables = self._tables()
        data = self._data
        if "head" in tables:
            self.units_per_em = struct.unpack_from(">H", data, tables["head"][0] + 18)[0]
        if "post" in tables:
            pos, thick, fixed = struct.unpack_from(">hHI", data, tables["post"][0] + 8)
            self.underline = (pos, thick)
            self.fixed_pitch = bool(fixed)
        if "cmap" not in tables:
            raise ValueError("no cmap table")
        off = tables["cmap"][0]
        n = struct.unpack_from(">H", data, off + 2)[0]
        # Prefer full-Unicode subtables over BMP-only ones: a face whose (3,10)
        # cmap lists the PUA while its (3,1) does not must still be usable.
        priority = {(3, 10): 0, (0, 6): 1, (0, 4): 2, (0, 3): 3, (3, 1): 4,
                    (0, 0): 5, (2, 10): 6, (3, 0): 7, (1, 0): 8}
        best = None
        for i in range(n):
            plat, enc, sub = struct.unpack_from(">HHI", data, off + 4 + 8 * i)
            prio = priority.get((plat, enc), 20)
            if best is None or prio < best[0]:
                best = (prio, sub)
        if best is None:
            raise ValueError("cmap has no encodings")
        self._read_cmap(off + best[1])

    def _read_cmap(self, sub: int) -> None:
        data = self._data
        fmt = struct.unpack_from(">H", data, sub)[0]
        if fmt in (12, 13):
            groups = struct.unpack_from(">I", data, sub + 12)[0]
            self._groups = [struct.unpack_from(">III", data, sub + 16 + 12 * i)
                            for i in range(groups)]
            self._unknown = False
        elif fmt == 4:
            seg = struct.unpack_from(">H", data, sub + 6)[0] // 2
            ends = struct.unpack_from(f">{seg}H", data, sub + 8)
            starts = struct.unpack_from(f">{seg}H", data, sub + 10 + 2 * seg)
            deltas = struct.unpack_from(f">{seg}h", data, sub + 10 + 4 * seg)
            ranges = struct.unpack_from(f">{seg}H", data, sub + 10 + 6 * seg)
            self._segments = list(zip(starts, ends, deltas, ranges))
            self._ro_base = sub + 10 + 6 * seg
            self._unknown = False
        else:
            # 0/2/6/8/10: rare today. Trust the font rather than claim coverage
            # we have not read.
            self._unknown = True

    # -- queries -----------------------------------------------------------
    def covers(self, ch: str) -> bool | None:
        """True/False from this face's own cmap; None when we cannot tell."""
        if self._unknown:
            return None
        cp = ord(ch)
        if self._groups is not None:
            for start, end, glyph_start in self._groups:
                if start <= cp <= end:
                    return glyph_start != 0
            return False
        if self._segments is not None:
            for i, (start, end, delta, rng) in enumerate(self._segments):
                if not (start <= cp <= end) or start == BMP:
                    continue
                if rng == 0:
                    return ((cp + delta) & 0xFFFF) != 0
                # idRangeOffset is a byte offset *relative to its own address*,
                # which is how the spec indexes glyphIdArray without storing it.
                at = self._ro_base + 2 * i + rng + 2 * (cp - start)
                if at + 2 > len(self._data):
                    return None
                g = struct.unpack_from(">H", self._data, at)[0]
                if g == 0:
                    return False
                return ((g + delta) & 0xFFFF) != 0
            return False
        return None


def load(path: str) -> Face:
    """Cached face load — coverage is queried once per cell per screenshot."""
    cache = load.__dict__.setdefault("_cache", {})
    face = cache.get(path)
    if face is None:
        face = cache[path] = Face(path)
    return face


def supports(font_path: str, text: str) -> bool | None:
    """Can this face draw every character of text? None = cannot determine."""
    face = load(font_path)
    verdict = True
    for ch in set(text):
        c = face.covers(ch)
        if c is None:
            return None
        if not c:
            verdict = False
    return verdict
