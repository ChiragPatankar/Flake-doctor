# flake-doctor

**Find the flaky tests hiding in your CI history, then prove what causes them and that the fix works.**

[![CI](https://github.com/ChiragPatankar/flake-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/ChiragPatankar/flake-doctor/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

Every team with CI has tests that fail for no reason. Someone clicks **Re-run**, it goes green, and
the flaky test survives to waste the next person's afternoon. Nobody keeps a list of which tests do
this, but your CI history already contains it: every time a job **failed and then passed on the same
commit**, a test flaked.

flake-doctor reads that history, tells you which pytest tests are flaky and what they cost, then
helps you diagnose and fix each one with evidence instead of `sleep()` and retries.

```
$ flake-doctor scan acme/shop
acme/shop: 812 runs, 2026-09-18 to 2026-10-18
flaky: 3   suspect: 1   real failures (hidden): 4   failed jobs: 57

[FLAKY] tests/test_cache.py::test_expiry
    passed on the same commit after failing 9x; 11 failures, 96 CI min, last 2026-10-17
    AssertionError: assert None == 'v'
[FLAKY] tests/integration/test_ws.py::test_reconnect
    retried by pytest-rerunfailures 14x; 2 failures, 18 CI min, last 2026-10-16
    ConnectionResetError: [Errno 54] Connection reset by peer
...
```
<sub>(Illustrative output; see "Results on real projects" below for real scans.)</sub>

## What it does

| Step | Command | How |
|------|---------|-----|
| **Find** | `flake-doctor scan` | Reads your GitHub Actions runs and job logs. A test is **flaky** when the same job passed on the same commit after it failed (a re-run, or a push and a PR run disagreeing), or when pytest-rerunfailures had to retry it. Tests failing on 3+ commits across 2+ branches are **suspects**. Failures confined to one branch look like real bugs and are kept out of the list. |
| **Diagnose** | `flake-doctor run`, `polluter`, `hints` | Reruns the test alone and in shuffled suite order (optionally with every CPU core busy, like a crowded CI runner) and classifies it: intrinsic, order-dependent, needs-other-tests, or not reproduced. For order-dependent tests it bisects to the exact test that leaks state. |
| **Fix** | the Claude Code plugin, or you | Fix the cause (fake the clock, wait for the thread, stop mutating the shared dict), not the symptom, then rerun enough times to show it's gone. |

## Quick start

```bash
pipx install flake-doctor        # or: uvx flake-doctor ...   (or pip install git+https://github.com/ChiragPatankar/flake-doctor)
export GITHUB_TOKEN=...          # any token that can read the repo's Actions; `gh auth login` also works
flake-doctor scan owner/repo     # or run it inside a clone and omit owner/repo
```

Useful options: `--days 60`, `--workflow tests` (only matching workflows), `--branch main`,
`--format markdown|json`, `--output report.md`, `--max-logs 400` (download budget; logs that can prove
flakiness are read first), `--scan-passing` (also read passing logs to catch rerunfailures retries),
`--all` (include likely-real failures), `--fail-on-flaky`.

Without a token, `scan` still lists **jobs** that failed and then passed on the same commit (that needs no
logs), which also works for projects that don't use pytest.

Job logs are cached in `~/.cache/flake-doctor`, so repeat scans only download what's new.

### As a GitHub Action

Get a **Flaky test report** issue that updates itself every week:

```yaml
# .github/workflows/flaky-tests.yml
name: Flaky test report
on:
  schedule:
    - cron: "0 6 * * 1"
  workflow_dispatch:

permissions:
  actions: read
  issues: write

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: ChiragPatankar/flake-doctor@v0
        with:
          days: 30
          workflow: tests          # optional: only your test workflow(s)
```

The report also goes to the job summary. Outputs: `flaky-count`, `suspect-count`, `report` (JSON path).
Set `fail-on-flaky: true` to make the job fail when flaky tests are found.

## Diagnosing one test locally

```
$ flake-doctor run "tests/test_checkout.py::test_order_total[standard]"

Isolated runs: 0/10 failed (0%)

Shuffled-suite runs: 13/20 failed (65%)
   13x  AssertionError: assert 104.29 == 129.11
  tests that change the outcome when they run before it:
    +1.00  tests/test_promotions.py::test_flash_sale_price[WID-100S-20]  (failed 13/13 when before, 0/7 when after)

Verdict: ORDER-DEPENDENT
  Passes alone but fails in some suite orders: another test leaks state into it.
  Next: flake-doctor polluter 'tests/test_checkout.py::test_order_total[standard]' --seed 868885 --scope tests
```

That's real output from [`examples/warehouse`](examples/warehouse): out of 176 tests, one parametrized
case of a promotions test mutates a dict returned by an `lru_cache`, which silently reprices the
product for every later test (and, in production, for every later customer).

| Command | What you get |
|---------|--------------|
| `flake-doctor run <node_id>` | failure rate alone and shuffled, grouped failure messages, tests correlated with failure, a verdict, and how many clean runs it takes to trust a fix. `--stress` loads every CPU core; `--json` saves results for before/after comparisons. |
| `flake-doctor polluter <node_id> --seed N` | the test that breaks it when run first, plus a two-test reproduction command. |
| `flake-doctor hints <node_id>` | suspicious lines in the test and the project code it imports: sleeps, real clocks, unseeded randomness, set/filesystem ordering, unrestored globals and env vars, network calls, fixed ports and paths. |

The local commands run your project's interpreter (auto-detecting `.venv`/`venv`; `--python` to override)
and switch off plugins that hide flakiness while measuring (pytest-randomly's own ordering, rerunfailures,
xdist). They never modify your files.

## Claude Code plugin

```
/plugin marketplace add ChiragPatankar/flake-doctor
/plugin install flake-doctor@flake-doctor
```

Then ask Claude things like *"CI keeps going red, which tests are flaky?"* or *"test_checkout fails on CI
but never locally, fix it."* The skill scans the CI history, measures before touching code, finds the
mechanism (with a deterministic reproduction where possible), fixes the cause rather than adding
retries, and verifies with enough reruns to make "it passed" mean something.
[`references/causes.md`](plugins/flake-doctor/skills/flake-doctor/references/causes.md) is a practical
guide to the causes and correct fixes, and worth reading on its own.

## How detection works, and its limits

- **Evidence, not guesses.** "Flaky" needs a same-commit pass after a failure, or a pytest-rerunfailures
  retry. Everything else is at most "suspect". Job identity is workflow name + job name (including the
  matrix values) + commit SHA.
- **pytest only, GitHub Actions only (for now).** Test names come from pytest's `FAILED`/`ERROR` summary
  lines (on by default), xdist progress lines, or `-v` output. Retries are attributed to tests from
  `RERUN` lines, which need `-v` or `-rR`.
- **Logs expire.** GitHub keeps job logs for 90 days by default; older failures are counted as
  "logs unavailable".
- **Projects that never re-run** produce fewer same-commit flips. Push and pull-request runs of the
  same commit still count, and suspects still surface.
- **API use.** One request per 100 runs, one per failed or re-run run, and one per job log read.
  Big test matrices can fail dozens of jobs at once, so logs are budgeted (`--max-logs`): jobs with a
  same-commit pass first, then at most two matrix siblings per run, newest first.

**Privacy:** flake-doctor only reads (except `--issue`, which writes one issue). Your token is sent only
to the GitHub API, never to the log storage host GitHub redirects to, and logs are cached locally.

## Results on real projects

_Coming with the first release: scans of popular open-source Python projects._

## Benchmarks

`evals/` contains the skill benchmark: realistic requests run by Claude with and without the skill
against [`examples/flaky-zoo`](examples/flaky-zoo) and the harder [`examples/warehouse`](examples/warehouse),
graded by scripts that rerun each fixed test 60–200 times and check the product code directly.

The honest result: on small, readable codebases a strong model fixes these flakes without the skill too.
The skill made verification more rigorous (fixes checked with 50+ reruns: 3/3 with the skill vs 1/3
without on the first set), but the outcome-changing piece is **finding** the flaky tests in the first
place, which needs CI history that no model can see by reading code. That's why `scan` is the core of
this project.

## Roadmap

- JUnit XML artifacts as an alternative to log parsing
- Jest/Vitest and Go test output
- GitLab CI
- Optional PR comments when a PR's failure matches a known flaky test

## Contributing

Issues and PRs welcome, especially log excerpts that the parser gets wrong. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
