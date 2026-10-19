from flake_doctor.logs import clean_line, parse_log

TS = "2026-10-12T14:03:22.4471234Z "


def gha(text):
    """Prefix every line with a GitHub Actions timestamp, like a downloaded job log."""
    return "\n".join(TS + line for line in text.strip("\n").splitlines())


def test_clean_line_strips_timestamp_and_ansi():
    assert clean_line(TS + "\x1b[31mFAILED\x1b[0m tests/a.py::t") == "FAILED tests/a.py::t"
    assert clean_line("﻿" + TS + "hello") == "hello"


def test_short_summary():
    log = gha("""
##[group]Run pytest
============================= test session starts ==============================
collected 412 items

tests/test_api.py ....F....                                             [ 40%]
=========================== short test summary info ============================
FAILED tests/test_api.py::test_login - AssertionError: assert 500 == 200
FAILED tests/test_api.py::test_search[unicode] - TimeoutError: timed out after 5s
ERROR tests/test_db.py::test_migrations - OperationalError: database is locked
================== 2 failed, 409 passed, 1 error in 61.20s (0:01:01) ===========
##[error]Process completed with exit code 1.
""")
    result = parse_log(log)
    assert result.ran_pytest
    assert result.failed == {
        "tests/test_api.py::test_login": "AssertionError: assert 500 == 200",
        "tests/test_api.py::test_search[unicode]": "TimeoutError: timed out after 5s",
    }
    assert result.errors == {"tests/test_db.py::test_migrations": "OperationalError: database is locked"}
    assert set(result.failing) == set(result.failed) | set(result.errors)


def test_xdist_progress_and_summary_messages_merge():
    log = gha("""
4 workers [300 items]
[gw1] [ 33%] FAILED tests/test_cache.py::test_expiry
[gw3] [ 34%] PASSED tests/test_cache.py::test_set
=========================== short test summary info ============================
FAILED tests/test_cache.py::test_expiry - assert None == 'v'
""")
    result = parse_log(log)
    assert result.failed == {"tests/test_cache.py::test_expiry": "assert None == 'v'"}


def test_verbose_mode_without_summary():
    log = gha("""
tests/test_a.py::test_one PASSED                                         [ 50%]
tests/test_a.py::test_two FAILED                                         [100%]
""")
    assert parse_log(log).failed == {"tests/test_a.py::test_two": ""}


def test_param_ids_with_spaces_in_summary():
    log = gha("FAILED tests/test_p.py::test_x[a b-1] - ValueError: nope")
    assert parse_log(log).failed == {"tests/test_p.py::test_x[a b-1]": "ValueError: nope"}


def test_rerunfailures_counts_reruns_once():
    log = gha("""
tests/test_net.py::test_fetch RERUN                                      [ 50%]
tests/test_net.py::test_fetch RERUN                                      [ 50%]
tests/test_net.py::test_fetch PASSED                                     [ 50%]
=========================== short test summary info ============================
RERUN tests/test_net.py::test_fetch
RERUN tests/test_net.py::test_fetch
================== 10 passed, 2 rerun in 3.10s ===================
""")
    result = parse_log(log)
    assert result.reruns == {"tests/test_net.py::test_fetch": 2}
    assert result.failed == {}


def test_failed_job_without_pytest():
    log = gha("""
Run ruff check .
src/app.py:3:1: F401 `os` imported but unused
Found 1 error.
##[error]Process completed with exit code 1.
""")
    result = parse_log(log)
    assert not result.ran_pytest
    assert result.failing == {}


def test_ignores_lines_that_merely_mention_failed():
    log = gha("""
Downloading FAILED_builds.txt
echo "FAILED to connect"
""")
    assert parse_log(log).failing == {}


def test_pytest_pretty_summary_table():
    log = gha("""
                                                    Summary of Failures
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃  File                        ┃  Function                             ┃  Function Line  ┃  Error Line  ┃  Error           ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│  tests/test_type_adapter.py  │  test_ta_config_used_for_json_schema  │  558            │  561         │  AssertionError  │
│  tests/test_models.py        │  TestCopy.test_deep                   │  10             │  12          │  KeyError        │
└──────────────────────────────┴───────────────────────────────────────┴─────────────────┴──────────────┴──────────────────┘
Results (83.02s):
         2 failed
""")
    result = parse_log(log)
    assert result.failed == {
        "tests/test_type_adapter.py::test_ta_config_used_for_json_schema": "AssertionError",
        "tests/test_models.py::TestCopy::test_deep": "KeyError",
    }


def test_failure_sections_when_summary_is_disabled():
    log = gha("""
collected 3 items
=================================== FAILURES ===================================
__________________________ TestCart.test_total[eur] ___________________________

    def test_total(self):
>       assert total() == 3
E       assert 2 == 3

tests/test_cart.py:42: AssertionError
_________________________________ test_fetch __________________________________
tests/test_net.py:7: TimeoutError
==================================== ERRORS ====================================
____________________ ERROR at setup of test_db_migrations _____________________
tests/test_db.py:3: OperationalError
____________________ ERROR collecting tests/test_broken.py _____________________
tests/test_broken.py:1: SyntaxError
========================= 2 failed, 1 error in 1.20s ==========================
""")
    result = parse_log(log)
    assert result.failed == {"tests/test_cart.py::TestCart::test_total[eur]": "AssertionError",
                             "tests/test_net.py::test_fetch": "TimeoutError"}
    assert result.errors == {"tests/test_db.py::test_db_migrations": "OperationalError"}


def test_summary_wins_over_sections():
    log = gha("""
=================================== FAILURES ===================================
_________________________________ test_fetch __________________________________
tests/test_net.py:7: TimeoutError
=========================== short test summary info ============================
FAILED tests/test_net.py::test_fetch - TimeoutError: read timed out
========================= 1 failed in 1.20s ==========================
""")
    assert parse_log(log).failed == {"tests/test_net.py::test_fetch": "TimeoutError: read timed out"}


def test_failure_sections_with_short_tracebacks():
    log = gha("""
=================================== FAILURES ===================================
__________________________________ test_total __________________________________
tests/test_cart.py:42: in test_total
    assert total() == 3
src/cart.py:9: in total
    raise ValueError("x")
E   ValueError: x
========================= 1 failed in 1.20s ==========================
""")
    assert parse_log(log).failed == {"tests/test_cart.py::test_total": ""}
