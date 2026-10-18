# term-use 2.0.0a7 verification

Box: Linux aarch64, tmux 3.8, Python 3.14, Pillow 12.3. The device run is
Termux/aarch64 with tmux 3.8 and Python 3.14.

## Unit

All `tests/unit/test_*.py` files pass (23 files), including the new
`test_doc_hygiene.py` and `test_quiet_partial.py` and the marker-attribution,
native-oracle, font-coverage and sentinel cases.

## Local e2e (tmux 3.8)

`tests/e2e/*.sh` run serially. 01-32 pass. `33_clobbered_shell_sentinel.sh`
initially failed because its state assertion still expected
`shell_integration: true`; after correcting it to `false` (term-use's sentinel
must not certify the shell as its own integration), 33 passes. Reruns of
13, 24, 25, 26, 27, 30, 31, 32 and 33 pass.

## Device (Termux, tmux 3.8)

13, 27, 31, 32, 33 and 22 pass. 22 reports a DroidSansMono primary with a
NotoSansCJK-Regular fallback and `missing: 0`; 31 exercises the real SIGSTOP
plant and the clobbered-shell marker case.

## Blind A/B

Task `general` (survey behind a clobbering rcfile, a background job, and a bash
copy named `myshell`), baseline the `e093271` skill directory, candidate this
branch's skill directory, k=2. All four arms reached `GENERAL_OK` with no
contaminated survey input. Weighted confusion 2.075 -> 1.75; `doc` 2.0 -> 1.0.
Tool-side observation bytes +3.6%, observations tied at 19, and wall clock is
load-confounded. The fixture and scorer are in this branch; the raw arm
recordings are archived in the maintainer's external evidence store.
