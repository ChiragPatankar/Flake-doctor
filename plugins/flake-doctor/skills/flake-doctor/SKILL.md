---
name: flake-doctor
description: Find, diagnose and fix flaky Python tests (pytest) with evidence. Ranks flaky tests from a repo's GitHub Actions history, measures how often a test fails by rerunning it in isolation and in shuffled suite orders, finds the test that pollutes another, pinpoints the root cause (timing, real clock, randomness, set/hash ordering, shared state, network, files/ports), fixes the cause rather than adding retries, and proves the fix with many clean reruns. Use this whenever a Python test is flaky, intermittent, nondeterministic, "sometimes fails", passes locally but fails on CI, passes alone but fails in the full suite (or the reverse), only passes on retry, or the user wants to stop rerunning CI to get green, even if they never say the word "flaky".
---

# flake-doctor

A flaky test fails sometimes without any code change. Guessing at the cause is how
teams end up with `sleep(5)` and `@retry` everywhere, so this skill works from
measurements: reproduce the flake, identify the mechanism, fix the cause, and show
before/after numbers. Users trust a fix they can see verified far more than one
that "should work".

The work is done with the `flake-doctor` CLI. If `flake-doctor --version` fails,
run it without installing via `uvx flake-doctor <command>`, or install it with
`pipx install flake-doctor` (or `pip install git+https://github.com/ChiragPatankar/flake-doctor`).
Run the local commands from the project root; they auto-detect `.venv`/`venv`
(pass `--python` otherwise), only run pytest, and never edit files.

| Command | Use it to |
|---------|-----------|
| `flake-doctor scan [owner/repo]` | rank flaky tests from GitHub Actions history (needs a GitHub token: `GITHUB_TOKEN`, `gh auth login`, or `~/.config/flake-doctor/token`) |
| `flake-doctor run <node_id>` | measure failure rate alone and in shuffled suite order; get a verdict |
| `flake-doctor polluter <node_id> --seed N` | find the test that breaks this one when it runs first |
| `flake-doctor hints <node_id>` | list suspicious lines (sleeps, clocks, random, sets, globals, network) in the test and the code it imports |
| `flake-doctor hints tests/ --only collection-time` | find clocks/randomness evaluated when tests are collected (e.g. `date.today()` in `parametrize`): flaky tests that may not have failed yet |

`references/causes.md` has fix patterns for each cause, plus the "fixes" that only
hide flakiness. Read the relevant section before writing a fix.

## Workflow

### 1. Pin down the test and confirm the setup

If the user doesn't know which tests are flaky ("CI is red half the time", "we keep
re-running jobs"), start with `flake-doctor scan` in the repo. It reads the CI
history and lists tests that failed and then passed on the same commit, with the
failure messages and CI minutes lost. Work through them in its order. Its
"suspect" tests failed on several branches but aren't proven flaky yet: measure
them before changing anything.

Get the pytest node id (`tests/test_x.py::test_y`, or `...::TestClass::test_y[param]`).
If the user pasted a CI log, take the node id and the failure message from it: the
message is evidence. Run the test once normally to check that the interpreter and
imports work before measuring anything.

For several flaky tests, measure each briefly first (`--runs 15 --shuffle-runs 10`)
and group them: tests failing with the same message often share one cause.

### 2. Measure before touching anything

```bash
flake-doctor run tests/test_x.py::test_y --json before.json
```

Defaults: 30 isolated runs (fresh process each) and 20 shuffled-suite runs over the
test's directory. Each run starts pytest, so for slow tests lower `--runs` or add
`--workers 4`, and tell the user roughly how long it will take.

Act on the verdict:

- **FLAKY-INTRINSIC**: fails alone, so the cause is inside the test or the code it calls. Go to step 3.
- **ORDER-DEPENDENT**: passes alone, fails in some orders. Run `flake-doctor polluter` with a failing seed and the same `--scope` (the script prints the exact command), then read what the polluter changes.
- **NEEDS-OTHER-TESTS**: fails alone, passes after some other test. The suspect list (negative lift) shows the test it silently depends on.
- **ALWAYS-FAILS**: not flaky. Say so and debug it as an ordinary failure.
- **NOT-REPRODUCED**: before changing code, try `--stress` (busy CPUs, like a crowded CI runner), more `--runs`, a wider `--scope` (the whole `tests/`), and the CI's conditions (timezone, env vars, `pytest -n`, Python version). If it still never fails, tell the user what you tried and what the numbers rule out. Don't edit a test you can't make fail unless the CI failure message plus the code make the cause unambiguous, and if you do, say the fix is unverified.

### 3. Find the mechanism

Run `flake-doctor hints` on the test, read the test and the code under test, and read the
matching section of `references/causes.md`. Then explain the failure concretely:
which two things race, which boundary is crossed, which state leaks from where.

Where you can, turn the flake into a **deterministic reproduction**: freeze the clock
just before a second boundary, force the colliding random values, run polluter then
victim, fix `PYTHONHASHSEED` to a failing value, or make the background work slower.
A reproduction that fails every time proves the diagnosis, and it often becomes the
regression test. Run it before writing the fix.

### 4. Fix the cause

Make the smallest change that removes the mechanism, matching the project's existing
tools and style (check whether it already uses freezegun, time-machine, responses,
factories, etc. before adding anything).

Decide whether the bug is in the test or in the product code. A test that randomly
catches duplicate coupon codes, a race, or an order-losing dedupe has found a real
bug, and fixing the test would hide it. In that case fix the code, keep the test strict,
and tell the user clearly that behaviour changes (see `references/causes.md` section 8).

Don't "fix" flakiness with retries, longer sleeps or timeouts, skip/xfail, forced test
order, a pinned seed or hash seed, or by deleting or loosening assertions. These lower
the failure rate on your machine and come back on CI. If one really is the only option
(say, an unmockable third-party dependency), explain why and label it a mitigation.

### 5. Verify with numbers

Rerun `flake-doctor run` with enough runs to make "it passed" meaningful: at least 3x the
`runs_for_95pct_confidence` it printed, and never fewer than 50 isolated / 30 shuffled.
Add `--stress` if the cause was timing. Save `--json after.json`. Then run the test's
file (or the full suite, if it's quick) once to make sure nothing else broke, and run
the deterministic reproduction from step 3 to show it now passes.

If it still fails, the diagnosis was incomplete: go back to step 3 with the new
failure messages rather than layering another change on top.

### 6. Report

Use this structure so the user can review it quickly and paste it into a PR:

```markdown
## Flaky test: `tests/test_x.py::test_y`

**Before:** 7/30 isolated runs failed (23%), 4/20 shuffled (20%). Verdict: FLAKY-INTRINSIC
**Root cause:** <category>: <2-4 sentences on the mechanism, with file:line references>
**Evidence:** <deterministic reproduction, polluter found, failure message pattern>
**Fix:** <what changed and why it removes the cause; flag product-code/behaviour changes>
**After:** 0/90 isolated (with CPU stress), 0/30 shuffled. At the old 23% rate, 90 clean runs
would happen by chance with probability < 0.0001%.
**Same pattern elsewhere:** <other spots `flake-doctor hints` flagged, if any>
```

The chance figure is `(1 - before_rate) ** after_runs`. Only claim what you measured.
