# Evidence — v2.0a6 review round (2026-10-17)

Artifacts behind the review comments on PR #6 and issue #7. Produced on this box:
Linux aarch64, tmux **3.7c** (so no native `pane_command_*` channel), Python 3.14,
Pillow 12.3.0, `term-use` 2.0.0a6 @ `7d953df`. Every run used a private `--socket` and a
mktemp `XDG_STATE_HOME`.

## `evidence/` — issue #5 (`wait --quiet-ms` vs OSC window-title updates)

Driver: a pane whose only output is `ESC ]0;<frame> BEL` every ~120 ms, never writing a cell.

- `A_titleonly.json` — `wait --quiet-ms 3000 --timeout 30` → `timeout`. `raw_tail` is all title
  bytes while `screen` stays 24 blank lines (`nonblank_lines: 0`), screen md5 unchanged across
  the window. This is the reproduction of #5 on this branch.
- `E_bash_titleonly.json` — same fault through a plain `bash` session (`python3 titleonly.py`),
  i.e. not specific to the ink TUI that reported it.
- `B_realspinner.json` — control: a spinner that really repaints, still `timeout`. Proves the raw
  gate was not over-loosened while "fixing" #5.
- `D_titlestops.json` — the same title writer stopped → `met` in 4.2 s with the screen still at
  `nonblank_lines: 0`. Pins the failure to Gate 1 (`waiting.py:615-621`), not to screen stability.
- `C_stilled.json` — positive control: ordinary output that goes quiet → `met`, `heuristic`.

## `images/` — issue #4 (SGR colors lost) and #3 (glyph coverage)

- `ctl-col.png` — the color-fidelity fixture used to close #4. Each row carries a **unique
  truecolor** (`fg #ff0a0a`, `bg #0a5abe`, `#0aff0a`, `#be0a5a`, …) so a color landing on the wrong
  row is visible in the pixel counts, plus `\e[7m` reverse, bold, dim, underline and strikethrough
  rows. 70x12 pane. Measured: fg `#ff0a0a` → `(255,10,10)` on rows [1,6]; bg `#0a5abe` → rows [2,6]  (row 6 is the reverse row); dim → `(0,102,0)` exactly a 50% blend; 1682 distinct colors.
- `col-A.png` — independently produced colored capture: 2017 colors / 173456 colored pixels, the
  six fixture colors' bounding boxes in fixture x-order, reverse row rendered black-on-white.

Both were produced with the repo's own `screenshot` command; neither is a mock or a re-render.

Note on what is **not** shown here: the #3 fallback defect is a discovery/ranking problem
(`screenshot.py:217-235` ranks candidate fonts by filename, `limit=4`), so on this box — which has
CJK faces that never get selected — a local render cannot demonstrate the Android/Termux case the
reporter described. No image in this directory should be read as closing #3.
