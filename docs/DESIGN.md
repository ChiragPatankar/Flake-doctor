# How flake-doctor works

This document explains the design: what each part does, and *why* each rule is the way it is. Most rules
exist because an earlier, simpler version gave a wrong answer on real CI data; those cases are called out.

- [The core idea](#the-core-idea)
- [Architecture](#architecture)
- [`scan`: finding flaky tests in CI history](#scan-finding-flaky-tests-in-ci-history)
- [`run`, `polluter`, `hints`: diagnosing one test](#run-polluter-hints-diagnosing-one-test)
- [Design constraints](#design-constraints)
- [Known limits](#known-limits)

## The core idea

A flaky test is one whose outcome changes **without the code changing**. "Without the code changing" is the
part you can actually check. A CI job is a function of (code, environment, luck). If the same job, on the same
commit, failed once and passed once, only the environment or luck changed, so whatever failed is flaky.

Most tools estimate flakiness from failure *rates*. Rates can't separate a flaky test from a real bug that
someone fixed a day later, or from a dependency release that broke every branch at once. A same-commit flip
can, which is why it's the only thing flake-doctor calls **flaky**.

## Architecture

```
src/flake_doctor/
  cli.py          argument parsing, wiring, exit codes
  github.py       stdlib-only GitHub REST client: pagination, rate limits, on-disk cache, safe redirects
  scan.py         collect(): runs -> jobs -> logs;  analyze(): evidence -> verdicts
  logs.py         pytest results from a raw job log
  report.py       text / Markdown / JSON renderers, issue upsert
  rerun.py        `run`: isolated and shuffled reruns, verdicts, suspect correlation
  polluter.py     `polluter`: bisection to the test that leaks state
  hints.py        `hints`: static scan for flakiness patterns (regex + AST)
  runner.py       runs pytest in a subprocess with flakiness-hiding plugins switched off
  _flake_doctor_plugin.py   tiny pytest plugin that reorders tests and reports outcomes as JSON
```

## `scan`: finding flaky tests in CI history

### 1. Listing runs

`GET /repos/{repo}/actions/runs` with `created=` and `status=completed`.

**GitHub returns at most 1,000 runs for any filtered listing**, whatever you ask for. The first version
asked for 30 days and silently got 2 (pandas), 5 (fastapi) or 10 (aiohttp). Now `_runs_in_window` asks for
`total_count` first (`per_page=1`) and, while it's above 1,000, splits the time window in half and recurses.
A busy repo's 30 days become a dozen windows of a few hundred runs each.

With `--workflow`, it resolves the matching workflow ids first and lists each workflow's runs. Filtering
afterwards would let other workflows eat the run budget.

### 2. Jobs: only where they can matter

Fetching every job of every run would cost thousands of requests. The jobs that can *prove* flakiness are
far fewer:

1. jobs of **failed runs**, and of **re-run runs** (every attempt: `/runs/{id}/attempts/{n}/jobs`);
2. jobs of **passing runs of the same workflow on a commit that also failed**, because those supply the
   "passed on the same commit" half of the evidence.

The "same workflow" restriction in (2) matters on repos that run several workflows per push; without it,
pydantic's scan fetched 240 unrelated passing runs.

A job's identity is `(workflow name, job name, commit SHA)`. The job name includes matrix values
(`test (3.12, ubuntu)`), so a failure on Windows is never "fixed" by a pass on Linux.

### 3. Logs, on a budget

Big matrices fail many jobs at once: 4 failed pydantic runs had 152 failed jobs. Each log can be megabytes.
`_prioritise_logs` reads, within `--max-logs`:

1. failed jobs whose same job passed on the same commit first, since their logs turn into proof;
2. then everything else, newest first, but **at most two matrix siblings per run**, because siblings
   usually fail the same way.

Logs are cached by URL in `~/.cache/flake-doctor`. Completed jobs and logs never change, so the cache never
needs invalidating; a repeat scan costs only the run listing (about 20 to 90 requests).

**Log downloads redirect to a storage host. The token is not forwarded there** (`github.py`,
`get_text`). urllib would forward the `Authorization` header across hosts by default, so redirects are
followed by hand, and a test checks this with two local servers.

### 4. Parsing pytest output (`logs.py`)

Sources, strongest first:

| Source | Example | Why it's needed |
|--------|---------|-----------------|
| short test summary | `FAILED tests/a.py::t[x] - AssertionError: ...` | pytest's default (`-rfE`) |
| xdist progress | `[gw2] [ 45%] FAILED tests/a.py::t` | parallel runs |
| verbose progress | `tests/a.py::t FAILED [ 45%]` | `-v` without a summary |
| pytest-pretty table | `│ tests/a.py │ test_x │ 558 │ 561 │ AssertionError │` | **pydantic**: the plugin replaces the summary, so the first version attributed 0 of 173 failed test jobs |
| failure sections | `____ TestCart.test_total[eur] ____` then `tests/test_cart.py:42: ...` | universal fallback, used only when no summary named anything |
| `RERUN` lines | `tests/a.py::t RERUN [ 50%]` | pytest-rerunfailures retries, counted once per rerun |

Lines are stripped of the GitHub timestamp prefix and ANSI colours. Windows runners sometimes emit cp1252
lines inside a UTF-8 log (`±` became `�`), so lines that aren't valid UTF-8 are decoded as cp1252.

A failed job with no pytest failures (lint, docs, setup, infra) is reported separately, never dropped
silently.

### 5. Verdicts (`analyze`)

| Verdict | Rule | Why |
|---------|------|-----|
| **flaky** | a failure in a job whose `(workflow, job, sha)` also passed, or any `RERUN` | direct evidence the outcome changed while the code didn't |
| **suspect** | failed on ≥3 commits across ≥2 branches **and** the workflow passed ≥50% of ≥5 runs within ±12 h of the failures | intermittent failures surrounded by green CI |
| **failing** (hidden) | everything else | probably a real bug (one branch) or a breakage (everything red) |

The pass-rate condition was added after fastapi, where the first suspect rule flagged 6 tests. Four of them
failed on 20 commits across 12 branches with the same anyio `DeprecationWarning`; the other two timed out on 4
commits across 2 branches. Measuring the workflow's pass rate around
each test's failures separated the cases cleanly:

| | workflow pass rate around the failures |
|---|---|
| aiohttp's confirmed flaky tests | 68% to 86% |
| fastapi's "suspects" | 13% (the anyio four) and 33% (the two timeouts) |

A flaky test fails *occasionally* while CI is otherwise green; a breakage fails *everything* until it's fixed.

CI minutes are attributed by splitting each failed job's duration evenly across the tests that failed in it.

## `run`, `polluter`, `hints`: diagnosing one test

### Controlled reruns (`runner.py`, `_flake_doctor_plugin.py`)

Each run is a fresh `pytest` subprocess of **the project's own interpreter** (`.venv`/`venv` auto-detected).
A small plugin, loaded with `-p _flake_doctor_plugin` from `PYTHONPATH`, reorders tests (by seed, or an exact
order file) and writes each target's outcome, phase and message to a JSON file. It's a standalone,
stdlib-only file so it can be imported by an interpreter that doesn't have flake-doctor installed.

Things that would hide flakiness are switched off while measuring:

- pytest-randomly's own ordering (`-p no:randomly`), so flake-doctor controls the order;
- pytest-rerunfailures (`--reruns 0`) and `flaky` (`-p no:flaky`), which would retry the failure away;
- xdist (`-n 0`), so order is meaningful;
- the cache provider, so `--lf`/`--ff` don't reorder;
- pytest-cov (`--no-cov`): found on aiohttp, whose settings enable coverage, where four parallel runs
  corrupted each other's coverage files and showed up as 7 bogus "failures";
- `PYTHONHASHSEED` is removed from the environment, so every run gets its own string-hash seed, as on CI.
  Set-ordering bugs (like `list(set(tags))`) only show up across processes.

`--stress` starts one busy loop per CPU core, like a crowded CI runner. In the warehouse example it takes a
rate-limiter race from a 2% failure rate on an idle laptop to 35%.

### Verdicts and statistics (`rerun.py`)

| isolated | shuffled | verdict |
|----------|----------|---------|
| always fails | always fails | ALWAYS-FAILS: a real failure |
| always fails | sometimes passes | NEEDS-OTHER-TESTS: depends on state another test sets up |
| sometimes fails | | FLAKY-INTRINSIC: the cause is inside the test or the code it calls |
| never fails | sometimes fails | ORDER-DEPENDENT: another test leaks state into it |
| never fails | never fails | NOT-REPRODUCED, with what that rules out (rule of three: 0 failures in n runs bounds the rate at about 3/n) |

To trust a fix, the number of clean runs needed for 95% confidence at the observed failure rate p is
`ceil(ln(0.05) / ln(1 - p))`; the tool recommends 3× that.

**Suspects.** For each other test, it compares the target's failure rate when that test ran *before* it with
when it ran *after*. A lift of +1.0 means the failure happens exactly when that test runs first. Ties are
real: tests' positions are correlated *through the victim's position* (if the victim runs late, most tests
ran before it), so a bystander can match a polluter perfectly by chance. flake-doctor's own CI caught that
(a bystander matched 8/8 vs 0/8). Tied suspects are flagged as tied instead of ranked arbitrarily, and the
output points to `polluter`, which decides.

### Polluter bisection (`polluter.py`)

Given a failing order, take the tests that ran before the victim and:

1. confirm prefix + victim fails, and the victim alone passes (otherwise it isn't order-dependent);
2. binary search: keep whichever half still makes the victim fail;
3. if neither half alone does (the failure needs tests from both), shrink one test at a time
   (a simple form of delta debugging).

Each check runs the order up to `--tries` times (default 3) and counts as failing if *any* run fails, so an
intermittently flaky victim doesn't send the search down the wrong half. In the warehouse example it narrows
70 tests that ran before the victim down to the one polluting parameter case in 29 pytest runs (7 seconds).

### Collection-time values (`collection.py`)

pytest imports every test module before running anything, so decorator arguments (`parametrize`, fixture
`params`), default arguments, and module- and class-level statements are evaluated once, at collection. When
such a value comes from the clock, a random generator or the process, the test later runs against a value that
is stale (`date.today()` from before midnight) or differs between processes (a random value, and with it the
*test ID* when it's a string parameter).

The check is an AST pass: it resolves import aliases (`from datetime import date`, `import datetime as dt`)
to dotted names, walks only the expressions that run eagerly (skipping lambda and function bodies), and
matches calls against clock, randomness and process lists. Seeded generators (`random.Random(42)`) are
fine. Environment reads are reported only when they become test data (in `parametrize`, fixture `params` or
defaults), because at module level or in a `skipif` they're usually deliberate configuration. In project
modules a test imports, module- and class-level values are reported as "import-time".

On pydantic's `tests/test_datetime.py` before the fix for pydantic#14030 it flags exactly the five lines
the fix changed. Across about 900 test files from pydantic, aiohttp, urllib3 and fastapi it reports five
values, one of which (a random UUID that ends up in a test ID) was an unreported problem.

### Hints (`hints.py`)

Regexes for sleeps, clocks, randomness, set/filesystem ordering, network, fixed ports and paths, plus AST checks
the regexes can't do: module-level mutable containers, and attribute assignments or mutating calls on
*imported* objects (`settings.currency = "EUR"`), which leak between tests. It follows one level of imports
into project code, because the cause is often in the code under test (the warehouse clock bug was in
`shop/reports.py`, not the test). Hints are leads, not verdicts.

## Design constraints

- **No runtime dependencies.** It runs in CI images, in other people's virtualenvs, and via `uvx` without
  dependency conflicts. The cost is a hand-written HTTP client.
- **Read-only by default.** The only write is `--issue`.
- **Never edits the user's code.** The local tools only run pytest.
- **Evidence over coverage.** It would rather miss a flaky test than call a real bug flaky; the hidden list is
  one flag away (`--all`).

## Known limits

- pytest on GitHub Actions only. JUnit XML artifacts, other test runners and other CI systems are on the
  roadmap.
- Projects that never re-run produce fewer same-commit flips (push and PR runs of the same commit still count).
- Logs expire after 90 days by default.
- `RERUN` attribution needs `-v` or `-rR`; with plain `-q`, rerunfailures only prints `R`.
- Suspect thresholds (3 commits, 2 branches, 50% of 5 runs, ±12 h) come from four projects' data; more data
  may move them.
