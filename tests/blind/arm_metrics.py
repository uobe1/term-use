#!/usr/bin/env python3
"""Cost metrics for one A/B arm, read out of its transcript without loading it.

    python3 tests/blind/arm_metrics.py LABEL=path/to/task-transcript[.output] ...

The agent transcript is the only place these numbers exist: term-use reports a
`context` block per observation, and the CLI's own process is short-lived, so the
byte cost the model actually paid is visible only in what the harness recorded.
Definitions, fixed here so two waves of a round stay comparable:

  observations        how many term-use results carried a `context` block
  tool_side_bytes     sum of those blocks' exact `bytes` — what the tool put on
                      the table, which is the number a skill revision can lower
  tool_side_est_tokens  the same, in the model's own currency
  transcript_bytes    the whole run, including the agent's own prose
  context_ratio_*     `context_usage_ratio` at the widest and final point: how
                      much of the window the task occupied
  wall_s              span of the record timestamps in the transcript. Recovered
                      because the completion notification is the only other place
                      wall clock lives and it is transcribed by hand — validated
                      to within 0.3% against a paired arm's reported 1333524 ms,
                      and it has already saved one wave whose notification arrived
                      without a usage block.

Cumulative tokens are *not* recoverable this way; the harness' completion
notification is the only source, so record it in the arm's `env.txt` the moment it
arrives.
"""
import re
import sys

PAIR = re.compile(r"bytes\D{0,8}(\d+)\D{0,120}?est_tokens\D{0,8}([0-9.]+)")
RATIO = re.compile(r"context_usage_ratio\D{0,6}([0-9.]+)")
STAMP = re.compile(r"[\"\\\\]+(?:timestamp|ts)[\"\\\\:]+ *([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z?)")


def wall_seconds(text):
    """First-to-last record timestamp, in seconds. Empty string if unparseable."""
    stamps = sorted(STAMP.findall(text))
    if len(stamps) < 2:
        return None
    from datetime import datetime
    try:
        a = datetime.fromisoformat(stamps[0].replace("Z", "+00:00"))
        b = datetime.fromisoformat(stamps[-1].replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((b - a).total_seconds(), 1)


def measure(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    pairs = PAIR.findall(text)
    ratios = [float(r) for r in RATIO.findall(text)]
    return {
        "observations": len(pairs),
        "tool_side_bytes": sum(int(b) for b, _ in pairs),
        "tool_side_est_tokens": int(sum(float(t) for _, t in pairs)),
        "transcript_bytes": len(text.encode()),
        "context_ratio_max": round(max(ratios), 4) if ratios else None,
        "context_ratio_last": round(ratios[-1], 4) if ratios else None,
        "wall_s": wall_seconds(text),
    }


def main(argv):
    bad = 0
    for arg in argv:
        label, _, path = arg.partition("=")
        try:
            m = measure(path if _ else arg)
        except OSError as exc:
            print(f"{label}: {exc}", file=sys.stderr)
            bad = 1
            continue
        print(f"{label:12s} obs={m['observations']:3d} bytes={m['tool_side_bytes']:6d} "
              f"est_tokens={m['tool_side_est_tokens']:6d} transcript={m['transcript_bytes']:7d} "
              f"ratio_max={m['context_ratio_max']} wall_s={m['wall_s']}")
    return bad


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
