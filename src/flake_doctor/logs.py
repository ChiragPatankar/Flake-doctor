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
# pytest-pretty's "Summary of Failures" table: │ file │ function │ line │ line │ error │
_PRETTY = re.compile(r"^[│|]\s*(\S+\.py)\s*[│|]\s*(\S+)\s*[│|]\s*\d+\s*[│|]\s*\d+\s*[│|]\s*(.*?)\s*[│|]$")
# pytest's own failure sections, present whatever summary plugin is used:
#   ________________ TestCart.test_total[eur] ________________
#   tests/test_cart.py:42: AssertionError
_SECTION = re.compile(r"^=+ (FAILURES|ERRORS) =+$")
_SECTION_END = re.compile(r"^=+ .* =+$")
_HEADER = re.compile(r"^_{3,} (.+?) _{3,}$")
_LOCATION = re.compile(r"^(\S+\.py):\d+: (?:in \S+|(\w[\w.]*))")   # "--tb=short" frames read "path:42: in test_x"
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


def _section_name(header):
    """'ERROR at setup of TestX.test_y[1]' -> ('error', 'TestX::test_y[1]'), or None for collection errors."""
    kind = "failed"
    for prefix in ("ERROR at setup of ", "ERROR at teardown of "):
        if header.startswith(prefix):
            kind, header = "error", header[len(prefix):]
    if header.startswith("ERROR collecting") or " " in header.split("[", 1)[0]:
        return None
    name, bracket, params = header.partition("[")
    return kind, name.replace(".", "::") + bracket + params


def parse_log(text):
    result = LogResult()
    summary_reruns = {}
    progress_reruns = {}
    sections = []          # (kind, name, file, exception) from FAILURES/ERRORS sections
    in_section = False
    current = None
    for raw in text.splitlines():
        line = clean_line(raw)
        if not line:
            continue
        if _SECTION.match(line.strip()):
            in_section = True
            continue
        if in_section:
            header = _HEADER.match(line.strip())
            if header:
                current = _section_name(header.group(1))
                if current:
                    sections.append([current[0], current[1], None, None])
                continue
            if _SECTION_END.match(line.strip()):
                in_section = False
            else:
                loc = _LOCATION.match(line.strip())
                if loc and current and sections and sections[-1][2] is None:
                    sections[-1][2], sections[-1][3] = loc.group(1), loc.group(2)
                continue
        pretty = _PRETTY.match(line.strip())
        if pretty and pretty.group(1) != "File":
            func = pretty.group(2).replace(".", "::")
            _record(result, "FAILED", "%s::%s" % (pretty.group(1), func), pretty.group(3))
            result.ran_pytest = True
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
    # Fall back to the failure sections only when no summary named the failures (summary disabled,
    # or a plugin replaced it): they lack class/parameter detail in some layouts, so the summary wins.
    if not result.failed and not result.errors:
        for kind, name, path, exc in sections:
            if path:
                _record(result, "FAILED" if kind == "failed" else "ERROR", "%s::%s" % (path, name), exc or "")
                result.ran_pytest = True
    for nodeid in set(summary_reruns) | set(progress_reruns):
        result.reruns[nodeid] = max(summary_reruns.get(nodeid, 0), progress_reruns.get(nodeid, 0))
    return result
