#!/usr/bin/env python3
"""A strict two-question survey: only clean answers produce a token."""
import sys


def ask(label):
    sys.stdout.write(label + "> ")
    sys.stdout.flush()
    line = sys.stdin.readline()
    return None if not line else line.rstrip("\n")


def fail(message):
    print(message)
    raise SystemExit(2)


name = ask("NAME")
if name != "alpha":
    fail("BAD NAME")
code = ask("CODE")
if code != "4242":
    fail("BAD CODE")
with open("result.txt", "w", encoding="utf-8") as fh:
    fh.write("TOKEN=TU-GENERAL-alpha\n")
print("SURVEY DONE")
