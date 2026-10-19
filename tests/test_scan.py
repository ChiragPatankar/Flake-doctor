import json

import pytest

from flake_doctor.github import GitHubError
from flake_doctor.report import to_json, to_markdown, to_text
from flake_doctor.scan import analyze, collect

REPO = "acme/shop"
WF = "CI"


def run(id_, sha, branch, conclusion, attempt=1, event="pull_request", created="2026-10-10T10:00:00Z"):
    return {"id": id_, "name": WF, "path": ".github/workflows/ci.yml", "head_sha": sha, "head_branch": branch,
            "event": event, "conclusion": conclusion, "run_attempt": attempt, "created_at": created}


def job(id_, name, conclusion, sha, minutes=10):
    return {"id": id_, "name": name, "conclusion": conclusion, "head_sha": sha,
            "started_at": "2026-10-10T10:00:00Z", "completed_at": "2026-10-10T10:%02d:00Z" % minutes,
            "html_url": "https://github.com/%s/actions/runs/x/job/%d" % (REPO, id_)}


def summary(*lines):
    return "collected 10 items\n=== short test summary info ===\n" + "\n".join(lines) + "\n=== 1 failed, 9 passed in 2.0s ===\n"


class FakeGitHub:
    def __init__(self, runs, jobs, logs, token="t"):
        self.token = token
        self.runs = runs
        self.jobs = jobs          # (run_id, attempt) -> [job]
        self.logs = logs          # job_id -> text, or an Exception to raise
        self.requests = 0
        self.log_requests = []

    def paginate(self, path, key, params=None, limit=None, cache=False):
        self.requests += 1
        if path.endswith("/actions/workflows"):
            return [{"id": 7, "name": WF, "path": ".github/workflows/ci.yml"},
                    {"id": 8, "name": "Docs", "path": ".github/workflows/docs.yml"}]
        if path.endswith("/actions/runs") or path.endswith("/workflows/7/runs"):
            return self.runs[:limit]
        if path.endswith("/workflows/8/runs"):
            return []
        parts = path.split("/")
        run_id, attempt = int(parts[parts.index("runs") + 1]), int(parts[parts.index("attempts") + 1])
        return self.jobs.get((run_id, attempt), [])

    def get_text(self, path, cache=False):
        self.requests += 1
        job_id = int(path.split("/")[-2])
        self.log_requests.append(job_id)
        value = self.logs.get(job_id, "collected 3 items\n=== 3 passed in 0.5s ===\n")
        if isinstance(value, Exception):
            raise value
        return value


def scenario():
    runs = [
        # 1: failed, re-run, passed on the same commit -> flaky
        run(1, "sha-a", "feature-x", "success", attempt=2),
        # 2, 3: a real regression on one branch -> hidden
        run(2, "sha-b", "fix-tax", "failure"),
        run(3, "sha-c", "fix-tax", "failure"),
        # 4, 5, 6: same test fails on three branches, never re-run -> suspect
        run(4, "sha-d", "feat-1", "failure"),
        run(5, "sha-e", "feat-2", "failure"),
        run(6, "sha-f", "feat-3", "failure"),
        # 7: lint failure, no pytest
        run(7, "sha-g", "feat-4", "failure"),
        # 8: passing run whose log shows pytest-rerunfailures retries
        run(8, "sha-h", "main", "success", event="push"),
        # 9, 10: push and pull_request runs of the same commit disagree -> flaky
        run(9, "sha-i", "feat-5", "failure", event="push"),
        run(10, "sha-i", "feat-5", "success"),
        # 11: failed job whose log has expired
        run(11, "sha-j", "feat-6", "failure"),
        # 12: cancelled runs are ignored
        dict(run(12, "sha-k", "feat-7", "failure"), conclusion="cancelled"),
    ]
    T = "test (3.12)"
    jobs = {
        (1, 1): [job(101, T, "failure", "sha-a", minutes=12), job(102, "lint", "success", "sha-a", 1)],
        (1, 2): [job(103, T, "success", "sha-a"), job(104, "lint", "success", "sha-a", 1)],
        (2, 1): [job(201, T, "failure", "sha-b")],
        (3, 1): [job(301, T, "failure", "sha-c")],
        (4, 1): [job(401, T, "failure", "sha-d")],
        (5, 1): [job(501, T, "failure", "sha-e")],
        (6, 1): [job(601, T, "failure", "sha-f")],
        (7, 1): [job(701, "lint", "failure", "sha-g", 1), job(702, T, "success", "sha-g")],
        (8, 1): [job(801, T, "success", "sha-h")],
        (9, 1): [job(901, T, "failure", "sha-i", minutes=8)],
        (10, 1): [job(1001, T, "success", "sha-i")],
        (11, 1): [job(1101, T, "failure", "sha-j")],
    }
    logs = {
        101: summary("FAILED tests/test_cache.py::test_expiry - assert None == 'v'",
                     "FAILED tests/test_api.py::test_slow - TimeoutError"),
        201: summary("FAILED tests/test_tax.py::test_rate - assert 0.07 == 0.065"),
        301: summary("FAILED tests/test_tax.py::test_rate - assert 0.07 == 0.065"),
        401: summary("FAILED tests/test_ws.py::test_reconnect - ConnectionResetError"),
        501: summary("FAILED tests/test_ws.py::test_reconnect - ConnectionResetError"),
        601: summary("FAILED tests/test_ws.py::test_reconnect - ConnectionResetError"),
        701: "Run ruff check .\nFound 3 errors.\n",
        801: "tests/test_net.py::test_fetch RERUN [ 50%]\ntests/test_net.py::test_fetch PASSED [ 50%]\n",
        901: summary("FAILED tests/test_cache.py::test_expiry - assert None == 'v'"),
        1101: GitHubError("GitHub API 410 for https://api.github.com/...: gone"),
    }
    return FakeGitHub(runs, jobs, logs)


def by_test(reports):
    return {r.nodeid: r for r in reports}


def test_verdicts():
    gh = scenario()
    jobs, info = collect(gh, REPO, quiet=True)
    reports, extra = analyze(jobs)
    tests = by_test(reports)

    assert info["runs"] == 11  # the cancelled run is dropped
    expiry = tests["tests/test_cache.py::test_expiry"]
    assert expiry.verdict == "flaky"
    assert len(expiry.flips) == 2          # re-run of run 1, and push vs PR run of sha-i
    assert expiry.shas == {"sha-a", "sha-i"}
    assert tests["tests/test_api.py::test_slow"].verdict == "flaky"
    assert tests["tests/test_ws.py::test_reconnect"].verdict == "suspect"
    assert tests["tests/test_tax.py::test_rate"].verdict == "failing"
    # rerunfailures only shows up when passing logs are read
    assert "tests/test_net.py::test_fetch" not in tests

    assert extra["failed_jobs_without_test_failures"] == {"CI / lint": 1}
    assert extra["failed_jobs_with_missing_logs"] == 1
    assert extra["rerun_attempts"] == 1
    # flaky tests come first, then suspects, then real failures
    assert [r.verdict for r in reports] == sorted([r.verdict for r in reports], key=["flaky", "suspect", "failing"].index)


def test_ci_minutes_are_split_between_tests_failing_in_the_same_job():
    gh = scenario()
    reports, _ = analyze(collect(gh, REPO, quiet=True)[0])
    tests = by_test(reports)
    # job 101 took 12 minutes and had two failing tests; job 901 took 8 minutes
    assert round(tests["tests/test_cache.py::test_expiry"].minutes, 1) == 14.0
    assert round(tests["tests/test_api.py::test_slow"].minutes, 1) == 6.0


def test_passing_runs_only_fetched_for_commits_that_failed():
    gh = scenario()
    collect(gh, REPO, quiet=True)
    assert 801 not in gh.log_requests      # passing run on an unrelated commit: skipped
    gh2 = scenario()
    jobs, _ = collect(gh2, REPO, scan_passing=True, quiet=True)
    reports, _ = analyze(jobs)
    fetch = by_test(reports)["tests/test_net.py::test_fetch"]
    assert fetch.verdict == "flaky" and fetch.reruns == 1


def test_workflow_filter():
    jobs, info = collect(scenario(), REPO, workflow="ci.yml", quiet=True)
    assert info["runs"] == 11
    jobs, info = collect(scenario(), REPO, workflow="docs", quiet=True)
    assert info["runs"] == 0 and jobs == []
    with pytest.raises(GitHubError, match="no workflow"):
        collect(scenario(), REPO, workflow="nightly", quiet=True)


def test_flaky_jobs_need_no_logs():
    gh = scenario()
    gh.token = None
    jobs, info = collect(gh, REPO, quiet=True)
    assert gh.log_requests == [] and info["logs_read"] is False
    reports, extra = analyze(jobs)
    assert reports == []
    assert extra["flaky_jobs"] == [{"job": "CI / test (3.12)", "failures": 8, "same_commit_passes": 2}]
    text = to_text(reports, info, extra)
    assert "need job logs, which need a GitHub token" in text
    assert "2/8   CI / test (3.12)" in text
    assert "Set GITHUB_TOKEN to see which tests flaked" in to_markdown(reports, info, extra)


def test_reports_render():
    gh = scenario()
    jobs, info = collect(gh, REPO, quiet=True)
    reports, extra = analyze(jobs)
    md = to_markdown(reports, info, extra)
    assert "**2 flaky tests** found" in md
    assert "`tests/test_cache.py::test_expiry` | flaky | passed on the same commit after failing 2x" in md
    assert "test_rate" not in md.split("### Notes")[0]          # real failure hidden from the table
    assert "1 test failed only on a single branch" in md
    assert "flake-doctor run 'tests/test_cache.py::test_expiry'" in md
    assert "test_rate" in to_markdown(reports, info, extra, show_all=True).split("### Notes")[0]

    data = json.loads(to_json(reports, info, extra))
    assert [t["verdict"] for t in data["tests"]] == ["flaky", "flaky", "suspect"]
    assert data["tests"][0]["same_commit_flips"] == 2

    text = to_text(reports, info, extra)
    assert "flaky: 2   suspect: 1" in text


def test_log_budget_reads_proof_first_and_limits_matrix_siblings():
    runs = [run(1, "sha-a", "b1", "success", attempt=2)] + [run(i, "sha-%d" % i, "b%d" % i, "failure") for i in range(2, 6)]
    jobs = {(1, 1): [job(11, "test (3.12)", "failure", "sha-a")], (1, 2): [job(12, "test (3.12)", "success", "sha-a")]}
    for i in range(2, 6):
        jobs[(i, 1)] = [job(i * 100 + k, "test (%d)" % k, "failure", "sha-%d" % i) for k in range(5)]
    gh = FakeGitHub(runs, jobs, {})
    jobs_out, info = collect(gh, REPO, quiet=True, max_logs=5)
    assert gh.log_requests[0] == 11                      # the job with a same-commit pass is read first
    assert len(gh.log_requests) == 5
    assert info["logs_skipped_by_budget"] == 21 - 5
    per_run = {}
    for job_id in gh.log_requests[1:]:
        per_run[job_id // 100] = per_run.get(job_id // 100, 0) + 1
    assert max(per_run.values()) <= 2                     # at most 2 matrix siblings per run
    _, extra = analyze(jobs_out)
    # only the 5 logs actually read count; skipped ones aren't mistaken for non-test failures
    assert sum(extra["failed_jobs_without_test_failures"].values()) == 5
