# Changelog

## 0.1.0 (unreleased)

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
