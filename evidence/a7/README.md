# term-use 2.0.0a7 verification

- Unit: `python3 tests/unit/test_*.py`
- E2E (tmux 3.8, serial): `bash tests/e2e/*.sh`, including
  `34_recorded_pane_guard.sh` and `35_sentinel_safety.sh`.

Device runs and blind A/B recordings are archived in the maintainer's external
evidence store; the harness, fixtures and scorer live in this branch.
