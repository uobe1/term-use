#!/usr/bin/env python3
"""Objective scorer for the ab_general fixture.

The task exercises the three shapes a fact channel can refuse: a program holding
the tty (marker paused), a background line (marker skipped), and a shell under a
name term-use cannot narrate. The end state is on disk; the recordings are read
only to check that an answer typed into the survey was not lengthened with shell
syntax.
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC_PATH = os.path.normpath(os.path.join(HERE, os.pardir, "fixtures",
                                          "ab_general", "protected", "spec.json"))


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return None


def injections(state_dirs):
    texts = []
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
                        texts.extend(str(item.get("text", ""))
                                     for item in ev[2] if isinstance(item, dict))
    return texts


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    app = argv[0]
    spec = json.load(open(SPEC_PATH))
    if not os.path.isdir(app) or not os.path.exists(os.path.join(app, "survey.py")):
        print("verdict=NO_ATTEMPT")
        return 0
    result = read(os.path.join(app, "result.txt"))
    bg = read(os.path.join(app, "bg.txt"))
    exit_code = read(os.path.join(app, "exit.txt"))
    own = read(os.path.join(app, "own.txt"))
    contaminated = [t for t in injections(argv[1:] or [])
                    if ("alpha" in t or "4242" in t)
                    and ("__tu_ec" in t or "printf" in t or ";" in t)]
    checks = {
        "result": result == spec["result"],
        "bg": bg == spec["bg"],
        "exit": exit_code == spec["exit"],
        "own": own == spec["own"],
        "clean_survey_input": not contaminated,
    }
    print(f"result={result!r} bg={bg!r} exit={exit_code!r} own={own!r}")
    print(f"contaminated_survey_input={len(contaminated)}")
    passed = sum(checks.values())
    verdict = ("GENERAL_OK" if passed == len(checks)
               else "PARTIAL" if passed else "NO_ATTEMPT")
    print(f"verdict={verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
