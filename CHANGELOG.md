# Changelog

## 0.2.0 (2026-10-21)

- `flake-doctor hints` finds **collection-time** values: clocks, randomness and process/environment reads
  evaluated when pytest collects tests (in `parametrize`, fixture `params`, default arguments, module and
  class level), plus **import-time** values in the project modules tests import. This is the pattern behind
  pydantic#14030 (`date.today()` in `parametrize`, failing when CI crosses midnight), and it needs no CI
  history, so it finds flaky tests before they fail. Suggested by a reader of the launch post on dev.to.
- `flake-doctor hints <folder>` audits every test file in a folder; `--only CATEGORY` filters, and `--fail`
  exits 1 when anything is found, for CI or pre-commit.
- `flake-doctor run` passes `--no-cov` when pytest-cov is installed: coverage slowed every rerun, and parallel
  reruns corrupted each other's coverage files, which looked like failures.
- Tied "suspects" (tests that match the failures equally well) are flagged as tied instead of ranked arbitrarily.
- A "Scan a repo" workflow runs scans and reruns on GitHub's machines.

## 0.1.0 (2026-10-19)

First release.

- `flake-doctor scan`: find flaky pytest tests in a repo's GitHub Actions history (same-commit
  failures that later passed, and pytest-rerunfailures retries), ranked with CI minutes lost.
  Text, Markdown and JSON output; can create/update a "Flaky test report" issue.
- `flake-doctor run`: rerun a test alone and in shuffled suite order, optionally under CPU load,
  and get a verdict (intrinsic, order-dependent, needs-other-tests, always-fails, not reproduced).
- `flake-doctor polluter`: bisect a failing order down to the test that breaks the victim.
- `flake-doctor hints`: flag sleeps, real clocks, randomness, set ordering, shared state and
  network calls in a test and the project code it imports.
- GitHub Action (`uses: ChiragPatankar/flake-doctor@v0`) for scheduled reports.
- Claude Code plugin with a diagnose-fix-verify skill.
