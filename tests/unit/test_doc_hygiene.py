#!/usr/bin/env python3
"""No change logs, version history, measurement anecdotes or round IDs in tracked text.

The maintainer's rule: rationale lives in the gitignored working notes, not in the
shipped docs, skill or code comments. A rule nobody checks decays, so this test
scans every tracked text file and fails with the file:line that reintroduced one.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP = {"tests/unit/test_doc_hygiene.py"}
SUFFIXES = (".md", ".py", ".sh", ".bashrc", ".toml", ".txt")

PATTERNS = {
    "measurement anecdote": re.compile(
        r"实测|measured\s*(?::|—|on\b|in\b)", re.I),
    "experiment round id": re.compile(r"\bab\d+\b|\b(?:round|wave)\s+\d+\b", re.I),
    "change log": re.compile(
        r"以前它会|\bpreviously (?:it|this)\b"
        r"|\bused to (?:render|produce|match|work|fail)\b", re.I),
    "old version history": re.compile(r"\b2\.0\.0a[1-5]\b|\bv2\.0a[1-5]\b|\ba[1-5]\b"),
}


def tracked_text():
    res = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    for name in res.stdout.splitlines():
        path = ROOT / name
        if name in SKIP or path.suffix not in SUFFIXES or not path.is_file():
            continue
        yield path


def main():
    hits = []
    for path in tracked_text():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{number}: "
                                f"[{label}] {line.strip()[:140]}")
    assert not hits, ("tracked text carries material that belongs in the "
                      "gitignored notes:\n" + "\n".join(hits))


if __name__ == "__main__":
    main()
    print("test_doc_hygiene: all passed")
