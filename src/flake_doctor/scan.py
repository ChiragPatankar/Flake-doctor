"""Find flaky tests in a repository's GitHub Actions history.

Evidence used, strongest first:
  same-commit flip  a test failed in a job, and the same job passed on the same commit
                    (a re-run, or another run of the same workflow) -> the code didn't change,
                    the outcome did
  rerun             pytest-rerunfailures had to retry the test (its RERUN lines in the log)
  spread            the test failed on 3+ commits across 2+ branches, which a real regression
                    on one branch rarely does (reported as "suspect", not proven)
"""

import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .github import GitHubError
from .logs import parse_log


def _ts(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if value else None


class Job:
    __slots__ = ("id", "run_id", "attempt", "workflow", "name", "sha", "branch", "event", "conclusion",
                 "minutes", "url", "created_at", "failing", "reruns", "ran_pytest", "log_missing", "log_read")

    def __init__(self, raw, run):
        self.id = raw["id"]
        self.run_id = run["id"]
        self.attempt = raw.get("run_attempt") or 1
        self.workflow = run.get("name") or run.get("path") or ""
        self.name = raw.get("name") or ""
        self.sha = raw.get("head_sha") or run["head_sha"]
        self.branch = run.get("head_branch") or ""
        self.event = run.get("event") or ""
        self.conclusion = raw.get("conclusion")
        start, end = _ts(raw.get("started_at")), _ts(raw.get("completed_at"))
        self.minutes = max((end - start).total_seconds() / 60, 0) if start and end else 0.0
        self.url = raw.get("html_url") or ""
        self.created_at = raw.get("completed_at") or run.get("created_at") or ""
        self.failing = {}
        self.reruns = {}
        self.ran_pytest = False
        self.log_missing = False
        self.log_read = False

    @property
    def key(self):
        return (self.workflow, self.name, self.sha)


def _progress(msg, quiet):
    if not quiet:
        sys.stderr.write(msg)
        sys.stderr.flush()


API_RESULT_CAP = 1000  # GitHub returns at most 1,000 runs for a filtered listing


def _fmt(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _runs_in_window(gh, path, params, start, end, limit):
    """All runs created in [start, end), splitting the window while GitHub's 1,000-result cap would bite."""
    window = dict(params, created="%s..%s" % (_fmt(start), _fmt(end - timedelta(seconds=1))))
    total = gh.get_json(path, dict(window, per_page=1)).get("total_count", 0)
    if total > API_RESULT_CAP and end - start > timedelta(minutes=30):
        middle = start + (end - start) / 2
        newer = _runs_in_window(gh, path, params, middle, end, limit)
        if len(newer) >= limit:
            return newer[:limit]
        return newer + _runs_in_window(gh, path, params, start, middle, limit - len(newer))
    return gh.paginate(path, "workflow_runs", window, limit=min(limit, API_RESULT_CAP)) if total else []


def _list_runs(gh, repo, params, workflow, max_runs, start, end):
    if not workflow:
        return _runs_in_window(gh, "/repos/%s/actions/runs" % repo, params, start, end, max_runs)
    needle = workflow.lower()
    workflows = [w for w in gh.paginate("/repos/%s/actions/workflows" % repo, "workflows")
                 if needle in (w.get("name") or "").lower() or needle in (w.get("path") or "").lower()]
    if not workflows:
        raise GitHubError("no workflow in %s matches %r" % (repo, workflow))
    runs = []
    for w in workflows:
        runs += _runs_in_window(gh, "/repos/%s/actions/workflows/%d/runs" % (repo, w["id"]), params, start, end,
                                max_runs)
    runs.sort(key=lambda r: r["created_at"], reverse=True)
    return runs[:max_runs]


def _prioritise_logs(candidates, passed_keys, max_logs, per_group=2):
    """Pick which job logs to read: proof of flakiness first, few matrix siblings per run, newest first."""
    def base(name):
        return name.split(" (", 1)[0]

    flips = sorted([j for j in candidates if j.key in passed_keys], key=lambda j: j.created_at, reverse=True)
    rest = sorted([j for j in candidates if j.key not in passed_keys], key=lambda j: j.created_at, reverse=True)
    chosen, groups = [], defaultdict(int)
    for job in flips + rest:
        group = (job.run_id, job.attempt, job.workflow, base(job.name))
        if job.key not in passed_keys and groups[group] >= per_group:
            continue
        if len(chosen) >= max_logs:
            break
        groups[group] += 1
        chosen.append(job)
    return chosen


def collect(gh, repo, days=30, workflow=None, branch=None, max_runs=5000, scan_passing=False, quiet=False,
            max_logs=400):
    """Fetch runs, jobs and failed-job logs. Returns (jobs, info)."""
    end = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=1)
    start = end - timedelta(days=days)
    since = start.strftime("%Y-%m-%d")
    params = {"status": "completed"}
    if branch:
        params["branch"] = branch
    _progress("listing workflow runs since %s... " % since, quiet)
    runs_listed = _list_runs(gh, repo, params, workflow, max_runs, start, end)
    runs = [r for r in runs_listed if r.get("conclusion") in ("success", "failure")]
    _progress("%d runs\n" % len(runs), quiet)

    def jobs_for(run):
        out = []
        for attempt in range(1, (run.get("run_attempt") or 1) + 1):
            raw_jobs = gh.paginate("/repos/%s/actions/runs/%d/attempts/%d/jobs" % (repo, run["id"], attempt),
                                   "jobs", cache=True)
            for raw in raw_jobs:
                raw.setdefault("run_attempt", attempt)
                out.append(Job(raw, run))
        return out

    jobs = []
    first_pass = [r for r in runs if r["conclusion"] == "failure" or (r.get("run_attempt") or 1) > 1]
    done = set()
    _progress("fetching jobs for %d failed or re-run runs" % len(first_pass), quiet)
    for i, run in enumerate(first_pass):
        jobs.extend(jobs_for(run))
        done.add(run["id"])
        if i % 20 == 19:
            _progress(".", quiet)
    _progress("\n", quiet)

    # A pass only proves flakiness for the same workflow on the same commit, so skip other workflows.
    failing = {(j.workflow, j.sha) for j in jobs if j.conclusion == "failure"}
    second_pass = [r for r in runs if r["id"] not in done
                   and (scan_passing or ((r.get("name") or r.get("path") or ""), r["head_sha"]) in failing)]
    if second_pass:
        _progress("fetching jobs for %d passing runs%s" % (
            len(second_pass), "" if scan_passing else " on the same commits"), quiet)
        for i, run in enumerate(second_pass):
            jobs.extend(jobs_for(run))
            if i % 20 == 19:
                _progress(".", quiet)
        _progress("\n", quiet)

    candidates = [j for j in jobs if j.conclusion == "failure" or (scan_passing and j.conclusion == "success")]
    passed_keys = {j.key for j in jobs if j.conclusion == "success"}
    to_read = _prioritise_logs(candidates, passed_keys, max_logs)
    skipped_logs = len(candidates) - len(to_read)
    if not getattr(gh, "token", True):
        # Log downloads need authentication even for public repos.
        for job in to_read:
            job.log_missing = True
        to_read = []
    _progress("reading %d job logs" % len(to_read), quiet)
    for i, job in enumerate(to_read):
        try:
            log = gh.get_text("/repos/%s/actions/jobs/%d/logs" % (repo, job.id), cache=True)
        except GitHubError as err:
            if " 404 " in str(err) or " 410 " in str(err) or "403" in str(err):
                job.log_missing = True  # expired (past retention) or not accessible
                continue
            raise
        parsed = parse_log(log)
        job.log_read = True
        job.failing = parsed.failing
        job.reruns = parsed.reruns
        job.ran_pytest = parsed.ran_pytest
        if i % 20 == 19:
            _progress(".", quiet)
    _progress("\n", quiet)

    info = {
        "repo": repo,
        "since": since,
        "runs": len(runs),
        "first_run": min((r["created_at"] for r in runs), default=None),
        "last_run": max((r["created_at"] for r in runs), default=None),
        "workflow_filter": workflow,
        "hit_max_runs": len(runs_listed) >= max_runs,
        "logs_read": bool(getattr(gh, "token", True)),
        "logs_skipped_by_budget": skipped_logs if getattr(gh, "token", True) else 0,
        "branch_filter": branch,
        "api_requests": gh.requests,
        # workflow, created_at, conclusion of every run: used to tell intermittent failures from breakages
        "_run_outcomes": [((r.get("name") or r.get("path") or ""), r["created_at"], r["conclusion"]) for r in runs],
    }
    return jobs, info


class TestReport:
    __test__ = False  # not a pytest test class

    def __init__(self, nodeid):
        self.nodeid = nodeid
        self.failures = []        # (job, message)
        self.flips = []           # failed jobs whose same job passed on the same commit
        self.reruns = 0
        self.rerun_jobs = []
        self.verdict = None
        self.minutes = 0.0
        self.workflow_pass_rate = None    # share of the workflow's runs that passed around the failures

    @property
    def shas(self):
        return {j.sha for j, _ in self.failures}

    @property
    def branches(self):
        return {j.branch for j, _ in self.failures}

    @property
    def workflows(self):
        return sorted({j.workflow for j, _ in self.failures} | {j.workflow for j in self.rerun_jobs})

    @property
    def last_seen(self):
        dates = [j.created_at for j, _ in self.failures] + [j.created_at for j in self.rerun_jobs]
        return max(dates) if dates else ""

    @property
    def messages(self):
        seen = []
        for _, msg in self.failures:
            if msg and msg not in seen:
                seen.append(msg)
        return seen

    @property
    def score(self):
        return 3 * len(self.flips) + 2 * self.reruns + len(self.shas)

    def evidence(self):
        parts = []
        if self.flips:
            parts.append("passed on the same commit after failing %dx" % len(self.flips))
        if self.reruns:
            parts.append("retried by pytest-rerunfailures %dx" % self.reruns)
        if not parts:
            parts.append("failed on %d commits across %d branches" % (len(self.shas), len(self.branches)))
            if self.workflow_pass_rate is not None:
                parts.append("the workflow passed %.0f%% of runs meanwhile" % (100 * self.workflow_pass_rate))
        return "; ".join(parts)

    def to_dict(self):
        return {
            "test": self.nodeid,
            "verdict": self.verdict,
            "evidence": self.evidence(),
            "failures": len(self.failures),
            "same_commit_flips": len(self.flips),
            "reruns": self.reruns,
            "commits": len(self.shas),
            "branches": sorted(self.branches),
            "workflows": self.workflows,
            "ci_minutes": round(self.minutes, 1),
            "last_seen": self.last_seen,
            "workflow_pass_rate": None if self.workflow_pass_rate is None else round(self.workflow_pass_rate, 2),
            "messages": self.messages[:5],
            "failed_jobs": [j.url for j, _ in self.failures][:10],
        }


SUSPECT_MIN_RUNS = 5           # runs of the workflow around the failures needed to judge intermittency
SUSPECT_MIN_PASS_RATE = 0.5    # below this the workflow was mostly red: a breakage, not a flake
SUSPECT_WINDOW = timedelta(hours=12)


def _pass_rate_around(report, run_outcomes):
    """Share of the failing workflow(s)' runs that passed within SUSPECT_WINDOW of the test's failures."""
    workflows = {j.workflow for j, _ in report.failures}
    times = sorted(_ts(j.created_at) for j, _ in report.failures if j.created_at)
    if not times:
        return None, 0
    lo, hi = times[0] - SUSPECT_WINDOW, times[-1] + SUSPECT_WINDOW
    span = [c for w, t, c in run_outcomes if w in workflows and lo <= _ts(t) <= hi]
    if not span:
        return None, 0
    return sum(c == "success" for c in span) / len(span), len(span)


def analyze(jobs, run_outcomes=None):
    """Group failures by test and decide which are flaky. Returns (reports, extra).

    run_outcomes: (workflow, created_at, conclusion) for every scanned run; when given, tests that
    failed widely while their workflow was mostly failing anyway are treated as breakages, not suspects.
    """
    passed = {j.key for j in jobs if j.conclusion == "success"}
    tests = {}

    def report(nodeid):
        if nodeid not in tests:
            tests[nodeid] = TestReport(nodeid)
        return tests[nodeid]

    infra = defaultdict(int)
    missing_logs = 0
    job_flips = defaultdict(lambda: [0, 0])     # "workflow / job" -> [failures, same-commit passes]
    for job in jobs:
        if job.conclusion == "failure":
            label = "%s / %s" % (job.workflow, job.name)
            job_flips[label][0] += 1
            if job.key in passed:
                job_flips[label][1] += 1
    for job in jobs:
        for nodeid, count in job.reruns.items():
            r = report(nodeid)
            r.reruns += count
            r.rerun_jobs.append(job)
        if job.conclusion != "failure":
            continue
        if job.log_missing:
            missing_logs += 1
            continue
        if not job.ran_pytest and not job.failing and not job.log_read:
            continue  # log skipped by the budget
        if not job.failing:
            infra["%s / %s" % (job.workflow, job.name)] += 1
            continue
        share = job.minutes / len(job.failing)
        for nodeid, msg in job.failing.items():
            r = report(nodeid)
            r.failures.append((job, msg))
            r.minutes += share
            if job.key in passed:
                r.flips.append(job)

    for r in tests.values():
        if r.flips or r.reruns:
            r.verdict = "flaky"
        elif len(r.shas) >= 3 and len(r.branches) >= 2:
            r.verdict = "suspect"
            if run_outcomes is not None:
                rate, n = _pass_rate_around(r, run_outcomes)
                r.workflow_pass_rate = rate
                if n < SUSPECT_MIN_RUNS or rate is None or rate < SUSPECT_MIN_PASS_RATE:
                    r.verdict = "failing"
        else:
            r.verdict = "failing"
    ordered = sorted(tests.values(), key=lambda r: ({"flaky": 0, "suspect": 1, "failing": 2}[r.verdict], -r.score, -r.minutes))
    extra = {
        "failed_jobs": sum(1 for j in jobs if j.conclusion == "failure"),
        "failed_jobs_without_test_failures": dict(sorted(infra.items(), key=lambda kv: -kv[1])),
        "failed_jobs_with_missing_logs": missing_logs,
        "rerun_attempts": len({(j.run_id, j.attempt) for j in jobs if j.attempt > 1}),
        "flaky_jobs": [{"job": label, "failures": f, "same_commit_passes": p}
                       for label, (f, p) in sorted(job_flips.items(), key=lambda kv: (-kv[1][1], -kv[1][0])) if p],
    }
    return ordered, extra
