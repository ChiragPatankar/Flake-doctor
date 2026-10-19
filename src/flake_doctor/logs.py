"""Extract pytest results from a GitHub Actions job log."""

import re

_TIMESTAMP = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ?")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# "FAILED tests/test_a.py::test_b[x] - AssertionError: ..." (short test summary, pytest -rfE default)
_SUMMARY = re.compile(r"^(FAILED|ERROR|RERUN)\s+(\S[^\s]*::.+?)(?:\s+-\s+(.*))?$")
# "[gw2] [ 45%] FAILED tests/test_a.py::test_b"  (xdist progress)
_XDIST = re.compile(r"^\[gw\d+\]\s+(?:\[\s*\d+%\]\s+)?(FAILED|ERROR|RERUN)\s+(\S+::\S+)")
# "tests/test_a.py::test_b FAILED   [ 45%]"  (pytest -v)
_VERBOSE = re.compile(r"^(\S+::\S+)\s+(FAILED|ERROR|RERUN)(?:\s+\[\s*\d+%\])?$")
_SESSION = re.compile(r"=+ test session starts =+|^collected \d+ items|^\d+ workers \[\d+ items\]")
_FINAL = re.compile(r"=+ .*\b(passed|failed|error|errors|no tests ran)\b.* in [\d.]+s")


class LogResult:
    """What one job log says about pytest: failed/errored/rerun node ids and their messages."""

    def __init__(self):
        self.failed = {}     # nodeid -> first message
        self.errors = {}     # nodeid -> first message (setup/teardown/collection errors)
        self.reruns = {}     # nodeid -> number of reruns (pytest-rerunfailures)
        self.ran_pytest = False

    @property
    def failing(self):
        merged = dict(self.errors)
        merged.update(self.failed)
        return merged

    def __repr__(self):
        return "LogResult(failed=%r, errors=%r, reruns=%r, ran_pytest=%r)" % (
            sorted(self.failed), sorted(self.errors), self.reruns, self.ran_pytest)


def clean_line(line):
    return _ANSI.sub("", _TIMESTAMP.sub("", line)).rstrip()


def _record(result, kind, nodeid, message):
    """Note a FAILED/ERROR node id; the summary line's message wins over a bare progress line."""
    nodeid = nodeid.strip()
    if "::" not in nodeid:
        return
    target = result.failed if kind == "FAILED" else result.errors
    if nodeid not in target or message:
        target[nodeid] = (message or "").strip()[:300]


def parse_log(text):
    result = LogResult()
    summary_reruns = {}
    progress_reruns = {}
    for raw in text.splitlines():
        line = clean_line(raw)
        if not line:
            continue
        if _SESSION.search(line) or _FINAL.search(line):
            result.ran_pytest = True
        stripped = line.strip()
        m = _SUMMARY.match(stripped)
        if m:
            kind, nodeid, message = m.group(1), m.group(2), m.group(3)
            if kind == "RERUN":
                summary_reruns[nodeid] = summary_reruns.get(nodeid, 0) + 1
            else:
                _record(result, kind, nodeid, message)
            result.ran_pytest = True
            continue
        m = _XDIST.match(stripped)
        if m:
            kind, nodeid = m.group(1), m.group(2)
        else:
            m = _VERBOSE.match(stripped)
            if not m:
                continue
            nodeid, kind = m.group(1), m.group(2)
        result.ran_pytest = True
        if kind == "RERUN":
            progress_reruns[nodeid] = progress_reruns.get(nodeid, 0) + 1
        else:
            _record(result, kind, nodeid, None)
    for nodeid in set(summary_reruns) | set(progress_reruns):
        result.reruns[nodeid] = max(summary_reruns.get(nodeid, 0), progress_reruns.get(nodeid, 0))
    return result
