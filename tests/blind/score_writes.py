#!/usr/bin/env python3
"""Objective scorer for the ab_write A/B fixture.

Reads the END STATE only — the ledger, the database, and the injections the
agent actually sent (from term-use's own raw.log). Nothing here looks at prose:
the point of the fixture is that a confident, wrong report is possible, so the
verdict has to come from disk.

Ground truth lives in `tests/fixtures/ab_write/protected/spec.json`, outside the
arm's writable tree. An agent that could edit the checker would pass it — the
BenchJack audit (4,458 tasks, every benchmark hackable) is why this is structured
around state predicates and a read-only spec rather than output matching.

    python3 tests/blind/score_writes.py /tmp/ab-old [STATE_DIR ...]
"""
import csv
import glob
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # <repo>/tests/blind
SPEC_PATH = os.path.normpath(os.path.join(HERE, os.pardir, "fixtures",
                                         "ab_write", "protected", "spec.json"))


def ledger_state(app, spec):
    path = os.path.join(app, "ledger.csv")
    if not os.path.exists(path):
        return {"rows": 0, "steps": [], "exists": False}
    with open(path, newline="") as fh:
        parsed = list(csv.reader(fh))
    header, body = (parsed[0], parsed[1:]) if parsed and parsed[0][:1] == ["seq"] \
        else ([], parsed)
    steps = [r[1] for r in body if len(r) > 1]
    want = spec["ledger"]["steps_in_order"]
    dupes = [s for s in set(steps) if steps.count(s) > 1]
    return {"rows": len(body), "steps": steps, "exists": True,
            "header_ok": header == spec["ledger"]["header"],
            "exact": steps == want, "missing": [s for s in want if s not in steps],
            "dupes": dupes}


def db_state(app, spec):
    want = spec["sync"]
    path = os.path.join(app, want["db"])
    if not os.path.exists(path):
        return {"exists": False, "exact": False}
    con = sqlite3.connect(path)
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        try:
            names = [r[0] for r in con.execute(
                f"SELECT name FROM {want['table']} ORDER BY name")]
        except sqlite3.OperationalError:
            names = []
    finally:
        con.close()
    return {"exists": True, "user_version": version, "rows": names,
            "exact": version == want["user_version"] and names == want["rows"]}


def injections(state_dirs):
    """Every command the agent injected, read out of term-use's own recording.

    Counting mentions in the injected text rather than counting `i` events keeps
    this fair to the agent that batches `deploy.py init && deploy.py build` into
    one send: what matters is how many times a step was asked for.

    Each path may be a session directory (`.../term-use/<name>`, the usual case —
    an auditor scoring one session of an arm) or its parent
    (`.../term-use`, scoring every session the arm opened).
    """
    text = ""
    for d in state_dirs:
        logs = ([os.path.join(d, "raw.log")] if os.path.exists(os.path.join(d, "raw.log"))
                else glob.glob(os.path.join(d, "*", "raw.log")))
        for raw in logs:
            with open(raw, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(ev, list) and len(ev) > 2 and ev[1] == "i":
                        text += json.dumps(ev[2], ensure_ascii=False)
    return {"deploy": sum(text.count(f"deploy.py {s}") for s in ("init", "build", "publish")),
            "run": text.count("sync.py run"), "check": text.count("sync.py check"),
            "sources": len(logs)}


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    app = argv[0]
    spec = json.load(open(SPEC_PATH))
    if not os.path.isdir(app) or not os.path.exists(os.path.join(app, "deploy.py")):
        print("verdict=NO_ATTEMPT")
        return 0
    led = ledger_state(app, spec)
    dbs = db_state(app, spec)
    inj = injections(argv[1:] or ["/nonexistent"])
    want = spec["expected_injections"]
    resends = max(0, inj["deploy"] - want["deploy_steps"]) \
        + max(0, inj["run"] - want["sync_run"])
    print(f"ledger rows={led['rows']} exact={int(led.get('exact', False))} "
          f"missing={','.join(led['missing']) or '-'} dupes={','.join(led['dupes']) or '-'}")
    print(f"db exists={int(dbs['exists'])} exact={int(dbs['exact'])} "
          f"rows={','.join(dbs.get('rows', []) or []) or '-'}")
    print(f"injections deploy={inj['deploy']}/{want['deploy_steps']} "
          f"run={inj['run']}/{want['sync_run']} check={inj['check']} "
          f"resends={resends} sessions={inj['sources']}")
    ledger_ok, db_ok = bool(led.get("exact", False)), bool(dbs["exact"])
    verdict = ("BOTH_OK" if ledger_ok and db_ok else
               "SYNC_INCOMPLETE" if ledger_ok else
               "LEDGER_BROKEN" if db_ok else
               "NEITHER")
    print(f"verdict={verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
