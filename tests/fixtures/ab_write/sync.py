#!/usr/bin/env python3
"""sync.py — the A/B fixture's stale anchor.

`check` is a preflight that prints `SYNC: COMPLETE` and writes nothing, because
that is what a preflight does: it says what a successful run WOULD look like.
`run` is the real thing: it stays silent for several seconds and then writes
`db.sqlite`.

So the screen is a trap. An agent that waits for `SYNC: COMPLETE` after starting
`run` is satisfied instantly by the preflight's own line — an anchor left behind
by an earlier command — and reports success while the database does not exist
yet. Two habits avoid it: scope the wait to output produced after this send, or
wait for the command itself and then read the artifact.
"""
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "db.sqlite")
ROWS = ("alpha", "beta", "gamma")
RUN_SECONDS = 6.0

cmd = sys.argv[1] if len(sys.argv) > 1 else ""

if cmd == "check":
    print("SYNC: COMPLETE")
    sys.exit(0)

if cmd == "run":
    time.sleep(RUN_SECONDS)
    con = sqlite3.connect(DB)
    try:
        con.execute("PRAGMA user_version = 7")
        con.execute("CREATE TABLE IF NOT EXISTS records (name TEXT PRIMARY KEY)")
        con.executemany("INSERT INTO records VALUES (?)", [(r,) for r in ROWS])
        con.commit()
    finally:
        con.close()
    sys.exit(0)

print("usage: sync.py <check|run>", file=sys.stderr)
sys.exit(2)
