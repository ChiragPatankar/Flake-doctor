"""pytest plugin loaded by flake-doctor's scripts via ``-p _flake_doctor_plugin``.

It is driven entirely by environment variables so the scripts can control a
pytest subprocess without touching the user's config:

FLAKE_DOCTOR_REPORT  path of a JSON file to write results to (required to activate)
FLAKE_DOCTOR_TARGETS newline-separated node ids whose outcomes should be recorded
                     (empty = record every test)
FLAKE_DOCTOR_SEED    integer; shuffle collected tests with this seed
FLAKE_DOCTOR_ORDER   path of a file with one node id per line; run exactly these
                     tests in exactly this order (takes precedence over SEED)
"""

import json
import os
import random

_REPORT = os.environ.get("FLAKE_DOCTOR_REPORT")
_TARGETS = {t for t in os.environ.get("FLAKE_DOCTOR_TARGETS", "").splitlines() if t}
_MAX_TRACE = 4000

_order = []
_results = {}


def _wanted(nodeid):
    return not _TARGETS or nodeid in _TARGETS


def pytest_collection_modifyitems(session, config, items):
    order_file = os.environ.get("FLAKE_DOCTOR_ORDER")
    seed = os.environ.get("FLAKE_DOCTOR_SEED")
    if order_file:
        with open(order_file) as f:
            wanted = [line.strip() for line in f if line.strip()]
        by_id = {item.nodeid: item for item in items}
        missing = [n for n in wanted if n not in by_id]
        if missing:
            raise RuntimeError(
                "flake-doctor: these tests were not collected: " + ", ".join(missing[:5])
            )
        items[:] = [by_id[n] for n in wanted]
    elif seed is not None:
        random.Random(int(seed)).shuffle(items)
    _order[:] = [item.nodeid for item in items]


def pytest_runtest_logreport(report):
    if not _REPORT or not _wanted(report.nodeid):
        return
    entry = _results.setdefault(
        report.nodeid, {"outcome": "passed", "phase": None, "duration": 0.0}
    )
    entry["duration"] += report.duration
    if report.when == "call" and report.skipped:
        entry["outcome"] = "skipped"
    if report.failed and entry["outcome"] != "failed":
        entry["outcome"] = "failed"
        entry["phase"] = report.when
        crash = getattr(report.longrepr, "reprcrash", None)
        entry["message"] = crash.message if crash is not None else str(report.longrepr)[:500]
        entry["traceback"] = report.longreprtext[-_MAX_TRACE:]


def pytest_sessionfinish(session, exitstatus):
    if not _REPORT:
        return
    with open(_REPORT, "w") as f:
        json.dump({"order": _order, "results": _results, "exitstatus": int(exitstatus)}, f)
