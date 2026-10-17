#!/usr/bin/env python3
"""The ab_write A/B fixture must be satisfiable, and its traps must actually
trap. A scorer nobody can satisfy is a bug; a trap that does not fire measures
nothing. Both are checked here without tmux (the end-to-end runs cover driving).
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

TESTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # <repo>/tests
FIXTURE = os.path.join(TESTS, "fixtures", "ab_write")
SCORER = os.path.join(TESTS, "blind", "score_writes.py")


def fresh_app():
    d = tempfile.mkdtemp(prefix="abfix-")
    app = os.path.join(d, "app")
    os.makedirs(app)
    for f in ("deploy.py", "sync.py"):
        shutil.copy(os.path.join(FIXTURE, f), app)
    return d, app


def inject(state_dir, *commands):
    """Write term-use `i` events so the scorer's resend count has something to read.

    Real arms keep them at `$XDG_STATE_HOME/term-use/<session>/raw.log`; the
    scorer is handed the directory one level above the session, so `s1` stands
    in for the session name.
    """
    os.makedirs(os.path.join(state_dir, "s1"), exist_ok=True)
    with open(os.path.join(state_dir, "s1", "raw.log"), "w") as fh:
        fh.write(json.dumps({"version": 2, "width": 80, "height": 24}) + "\n")
        for i, cmd in enumerate(commands, 2):
            fh.write(json.dumps([i * 0.5, "i", [{"text": [cmd]}]]) + "\n")


def score(app, state_dir):
    out = subprocess.run([sys.executable, SCORER, app, state_dir],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_happy_path_is_satisfiable():
    d, app = fresh_app()
    try:
        for step in ("init", "build", "publish"):
            r = subprocess.run([sys.executable, os.path.join(app, "deploy.py"), step],
                               capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            assert r.stdout == "", f"a silent step must stay silent: {r.stdout!r}"
        assert subprocess.run([sys.executable, os.path.join(app, "sync.py"), "check"],
                              capture_output=True, text=True).stdout.strip() \
            == "SYNC: COMPLETE"
        assert subprocess.run([sys.executable, os.path.join(app, "sync.py"), "run"],
                              capture_output=True, text=True).stdout == ""
        steps = [r[1] for r in list(csv.reader(open(os.path.join(app, "ledger.csv"))))[1:]]
        assert steps == ["init", "build", "publish"], steps
        report = score(app, os.path.join(d, "state"))
        assert "verdict=BOTH_OK" in report, report
        assert "resends=0" in report, report
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_resending_a_step_is_caught():
    d, app = fresh_app()
    try:
        for step in ("init", "build", "publish"):
            subprocess.run([sys.executable, os.path.join(app, "deploy.py"), step],
                           capture_output=True)
        # the classic mistake: nothing printed, so send it again
        subprocess.run([sys.executable, os.path.join(app, "deploy.py"), "publish"],
                       capture_output=True)
        subprocess.run([sys.executable, os.path.join(app, "sync.py"), "run"],
                       capture_output=True)
        state = os.path.join(d, "state")
        inject(state, "deploy.py init", "deploy.py build", "deploy.py publish",
               "deploy.py publish", "sync.py run")
        report = score(app, state)
        assert "verdict=LEDGER_BROKEN" in report, report
        assert "dupes=publish" in report, report
        assert "resends=1" in report, report
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_trusting_the_preflight_text_is_caught():
    d, app = fresh_app()
    try:
        for step in ("init", "build", "publish"):
            subprocess.run([sys.executable, os.path.join(app, "deploy.py"), step],
                           capture_output=True)
        subprocess.run([sys.executable, os.path.join(app, "sync.py"), "check"],
                       capture_output=True)
        state = os.path.join(d, "state")
        inject(state, "sync.py check")
        report = score(app, state)
        # the anchor the agent would have waited on is on screen; the db is not
        assert "verdict=SYNC_INCOMPLETE" in report, report
        assert os.path.exists(os.path.join(app, "ledger.csv"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_stale_anchor_is_a_real_hazard():
    """The trap depends on `check`'s line being exactly what a run would show."""
    src = open(os.path.join(FIXTURE, "sync.py")).read()
    assert "print(\"SYNC: COMPLETE\")" in src, "preflight must paint the anchor"
    assert "SYNC: COMPLETE" not in src.split('cmd == "run"')[1], \
        "the real run must print nothing — otherwise there is no stale anchor"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
    print("all tests passed")
