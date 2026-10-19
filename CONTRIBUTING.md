# Contributing

Thanks for helping! Bug reports with a real CI log excerpt (FAILED/RERUN lines) are especially useful,
because log formats vary between pytest plugins and CI setups.

## Development

```bash
git clone https://github.com/ChiragPatankar/flake-doctor
cd flake-doctor
python -m venv .venv && . .venv/bin/activate
pip install -e . pytest
pytest
```

The package has no runtime dependencies; please keep it that way. It supports Python 3.9+.
The pytest plugin in `src/flake_doctor/_flake_doctor_plugin.py` runs inside *your project's* interpreter,
so it must stay standard-library only.

## Project layout

| Path | What |
|------|------|
| `src/flake_doctor/scan.py`, `github.py`, `logs.py`, `report.py` | `flake-doctor scan` (CI history) |
| `src/flake_doctor/rerun.py`, `polluter.py`, `hints.py`, `runner.py` | local diagnosis commands |
| `action.yml` | the GitHub Action |
| `plugins/flake-doctor/` | the Claude Code plugin and skill |
| `examples/flaky-zoo`, `examples/warehouse` | sample projects with known flaky tests (used by tests and evals) |
| `evals/` | skill benchmark prompts and the grader |
