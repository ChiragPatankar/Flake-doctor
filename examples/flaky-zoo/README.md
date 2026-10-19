# Flaky zoo

A tiny shop backend whose test suite is flaky in the ways real suites are.
It's the benchmark and demo project for flake-doctor. **Spoilers below.**

| Test | Fails | Root cause |
|------|-------|------------|
| `tests/test_reports.py::test_report_timestamp` | ~15% | Real clock: crosses a second boundary while the report is built |
| `tests/test_exports.py::test_export_finishes` | ~40% | Timing: fixed `sleep()` races a background thread |
| `tests/test_tags.py::test_unique_tags_keeps_order` | ~50% | Hash ordering: dedupes with `set()`, order varies per process |
| `tests/test_coupons.py::test_codes_are_unique` | ~18% | Randomness exposing a real bug: code space too small, no collision check |
| `tests/test_pricing.py::test_default_currency` | only in suite | Order-dependent: `test_admin.py::test_switch_store_to_euro` changes a global and never restores it |
| `tests/test_cart.py::test_cart_total` | never | Stable control test (for "it flaked once on CI" reports) |

Run everything: `python -m pytest` (from this folder).
