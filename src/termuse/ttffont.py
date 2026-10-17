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
        # One entry per cmap subtable we could read: (segments, ro_base) for
        # format 4, groups for format 12/13. Kept separate because
        # idRangeOffset is relative to an address inside its own subtable.
        self._fmt4: list[tuple[list[tuple[int, int, int, int]], int]] = []
        self._fmt12: list[list[tuple[int, int, int]]] = []
        self._unknown = True
        self._data = b""
        try:
            with open(path, "rb") as fh:
                self._data = fh.read()
            self._parse()
        except Exception as exc:  # a strange font must not kill the channel
            self.error = f"{type(exc).__name__}: {exc}"
            self._fmt4, self._fmt12 = [], []
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
        # Read every subtable we understand and union the coverage. Picking one
        # "best" encoding is a guess about which one a given rasterizer will
        # use, and a face that lists more in (0,3) than (3,1) (or the reverse)
        # would then be judged by the wrong map.
        for i in range(n):
            plat, enc, sub = struct.unpack_from(">HHI", data, off + 4 + 8 * i)
            self._read_cmap(off + sub)
        if not (self._fmt4 or self._fmt12):
            raise ValueError("no readable cmap subtable")

    def _read_cmap(self, sub: int) -> None:
        data = self._data
        fmt = struct.unpack_from(">H", data, sub)[0]
        if fmt in (12, 13):
            groups = struct.unpack_from(">I", data, sub + 12)[0]
            self._fmt12.append([struct.unpack_from(">III", data, sub + 16 + 12 * i)
                                for i in range(groups)])
            self._unknown = False
        elif fmt == 4:
            # Subtable header is format/length/language/segCountX2 and then
            # THREE more UINT16s (searchRange, entrySelector, rangeShift)
            # before endCode[] — skipping them shifts every later offset by 6
            # bytes, which reads ASCII fine (it lands in a delta segment) and
            # silently mis-covers the rest of the BMP.
            seg = struct.unpack_from(">H", data, sub + 6)[0] // 2
            ends = struct.unpack_from(f">{seg}H", data, sub + 14)
            starts = struct.unpack_from(f">{seg}H", data, sub + 16 + 2 * seg)
            deltas = struct.unpack_from(f">{seg}h", data, sub + 16 + 4 * seg)
            ranges = struct.unpack_from(f">{seg}H", data, sub + 16 + 6 * seg)
            self._fmt4.append((list(zip(starts, ends, deltas, ranges)),
                               sub + 16 + 6 * seg))
            self._unknown = False
        else:
            # 0/2/6/8/10: rare today. Trust the font rather than claim coverage
            # we have not read.
            self._unknown = True

    # -- queries -----------------------------------------------------------
    def covers(self, ch: str) -> bool | None:
        """True/False from this face's own cmap; None when we cannot tell.

        Union across subtables: any map that resolves the codepoint to a
        non-zero glyph means the face can draw it.
        """
        if self._unknown:
            return None
        cp = ord(ch)
        for groups in self._fmt12:
            for start, end, glyph_start in groups:
                if start <= cp <= end and glyph_start != 0:
                    return True
        for segments, ro_base in self._fmt4:
            for i, (start, end, delta, rng) in enumerate(segments):
                if not (start <= cp <= end) or start == BMP:
                    continue
                if rng == 0:
                    if ((cp + delta) & 0xFFFF) != 0:
                        return True
                    break
                # idRangeOffset is a byte offset *relative to its own address*,
                # which is how the spec indexes glyphIdArray without storing it.
                at = ro_base + 2 * i + rng + 2 * (cp - start)
                if at + 2 > len(self._data):
                    return None
                g = struct.unpack_from(">H", self._data, at)[0]
                if g != 0 and ((g + delta) & 0xFFFF) != 0:
                    return True
                break
        return bool(self._fmt12 or self._fmt4) and False


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
