"""Structured error taxonomy: every failure surfaces as JSON {code,message,hint,evidence}."""
import json

# Design doc section 6 — the canonical code table.
ERROR_CODES = (
    "session-missing",
    # CLI argument validation failure (--repeat < 1, bad hex, unknown key, ...).
    "invalid-arguments",
    "socket-unreachable",
    # tmux reported a failure we could not fingerprint (nonzero exit or error
    # text on stderr); its own message rides along so the case stays diagnosable.
    "tmux-failed",
    "pane-dead",
    "pane-dead-by-signal",
    "no-shell-integration",
    # wait on a pane that exists in tmux but has no term-use records
    # (e.g. nested tmux): screen channels degrade, shell-protocol facts refuse.
    "unmanaged-pane",
    # wait --shell-ready: the `true` probe got no OSC 133 answer in time
    # (a live interactive program swallows the probe, or the shell is slow).
    "shell-not-ready",
    "expect-code-mismatch",
    "timeout",
    "mouse-not-enabled",
    "tty-echo-broken",
    # Image channel: Pillow unavailable (or disabled via env switch).
    "pillow-missing",
    # Image channel: no monospace font to draw the cell grid with
    # (also raised when TERM_USE_FONT points at a nonexistent file).
    "font-missing",
)


class TDError(Exception):
    """A term-use failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str, hint: str | None = None,
                 evidence: dict | None = None):
        super().__init__(message)
        assert code in ERROR_CODES, f"unknown error code: {code}"
        self.code = code
        self.message = message
        self.hint = hint
        self.evidence = evidence

    def to_json(self) -> str:
        return json.dumps(
            {"error": {
                "code": self.code,
                "message": self.message,
                "hint": self.hint,
                "evidence": self.evidence,
            }},
            ensure_ascii=False,
        )

    @property
    def exit_code(self) -> int:
        return 1
