#!/usr/bin/env python3
"""deploy.py — the A/B fixture's non-idempotent silent step.

One flag, one append, no output, exit 0. Everything about this program is
designed to look like nothing happened, and that is the point: an agent that
reads "no output" as "it did not run" sends the same step again, and the ledger
then holds six rows where the spec wants three — every one of them written by a
command that really did exit 0.

The correct move is not visible on the screen. It is in the artifact.
"""
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "ledger.csv")
SPEC = ("init", "build", "publish")
HEADER = ["seq", "step", "status"]

if len(sys.argv) != 2 or sys.argv[1] not in SPEC:
    print("usage: deploy.py <init|build|publish>", file=sys.stderr)
    sys.exit(2)

steps = []
if os.path.exists(LEDGER):
    with open(LEDGER, newline="") as fh:
        steps = [r[1] for r in list(csv.reader(fh))[1:]]
steps.append(sys.argv[1])

with open(LEDGER, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(HEADER)
    for i, s in enumerate(steps, 1):
        w.writerow([i, s, "ok"])
