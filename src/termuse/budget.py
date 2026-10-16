"""Context accounting: what an observation costs the agent's context window.

An agent's reads are its bill. A 120x40 pane dump or a timeout evidence
snapshot costs the same thousands of tokens every time it is taken, and a
stale screen re-carries its history on the next capture — so the tool states
the price, says when a read is over budget, and names the way out (`clear`).

Estimates, honestly labeled (every block carries a `method` field):
  text  — ~4 bytes per token. Crude for box-drawing/CJK-heavy screens and for
          the SGR grid JSON (several times denser than plain text), and being
          pessimistic is the safe direction: an agent that clears early loses
          nothing.
  image — pixels/750, the size rule quoted for Anthropic vision requests. The
          provider pages could not be fetched from the region this was written
          in, so treat it as an order of magnitude; `count_tokens` is the
          authority. `bytes`/`image_bytes` are exact and are what to trust.

`TERM_USE_CONTEXT_BUDGET` (tokens) overrides the default 2000 for hosts with
different budgets; 0 disables the warning without hiding the numbers.
"""
import os
import sys

DEFAULT_BUDGET_TOKENS = 2000

BYTES_PER_TOKEN = 4
IMAGE_TOKEN_DIVISOR = 750


def budget() -> int:
    raw = os.environ.get("TERM_USE_CONTEXT_BUDGET")
    if raw is None:
        return DEFAULT_BUDGET_TOKENS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_BUDGET_TOKENS


def est_text_tokens(nbytes: int) -> int:
    return -(-nbytes // BYTES_PER_TOKEN)  # ceil


def est_image_tokens(pixels: list[int]) -> int:
    return max(1, round(pixels[0] * pixels[1] / IMAGE_TOKEN_DIVISOR))


def _cap(est: int, method: str) -> dict:
    b = budget()
    return {"est_tokens": est, "budget": b, "method": method,
            "over_budget": b > 0 and est > b}


TEXT_METHOD = ("bytes/4 — an estimate, not a meter: box-drawing, CJK and the "
               "SGR grid JSON cost noticeably more per byte than prose")
IMAGE_METHOD = ("pixels/750 — the size rule commonly quoted for Anthropic vision "
                "requests; the provider's own count_tokens is authoritative")


def text_context(text: str, extra: dict | None = None) -> dict:
    lines = text.splitlines()
    nbytes = len(text.encode("utf-8"))
    ctx = {"lines": len(lines),
           "nonblank_lines": sum(1 for ln in lines if ln.strip()),
           "bytes": nbytes}
    ctx.update(_cap(est_text_tokens(nbytes), TEXT_METHOD))
    if extra:
        ctx.update(extra)
    return ctx


def image_context(path: str, pixels: list[int], extra: dict | None = None) -> dict:
    try:
        nbytes = os.path.getsize(path)
    except OSError:
        nbytes = 0
    ctx = {"image_bytes": nbytes, "pixels": list(pixels)}
    ctx.update(_cap(est_image_tokens(pixels), IMAGE_METHOD))
    if extra:
        ctx.update(extra)
    return ctx


def warn(ctx: dict, target: str, channel: str = "text") -> bool:
    """Over-budget notice on stderr; returns whether it fired.

    stderr, not stdout: `screen | grep` and `screen --grid | jq` must keep
    parsing cleanly, and a warning that breaks the pipe is a new bug class.
    """
    if not ctx.get("over_budget"):
        return False
    est, b = ctx["est_tokens"], ctx["budget"]
    if channel == "image":
        detail = f"~{est} vision tokens for a {ctx['pixels'][0]}x{ctx['pixels'][1]} px image"
    else:
        detail = f"~{est} tokens across {ctx.get('lines', '?')} lines"
    print(f"warning: this {channel} capture costs {detail} (context budget "
          f"{b}, set TERM_USE_CONTEXT_BUDGET): every later capture re-carries "
          f"it — reclaim the pane with `term-use clear -n {target}`, or narrow "
          f"the read (--scrollback N, -J, --grep, or the screenshot channel "
          f"for layout questions)", file=sys.stderr)
    return True
