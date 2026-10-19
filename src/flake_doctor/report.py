"""Render scan results as Markdown, plain text or JSON, and keep a GitHub issue up to date."""

import json

from . import __version__

ISSUE_TITLE = "Flaky test report"
ISSUE_LABEL = "flaky-tests"
HOME = "https://github.com/ChiragPatankar/flake-doctor"


def _short(nodeid, width=90):
    return nodeid if len(nodeid) <= width else "…" + nodeid[-(width - 1):]


def _md_escape(text):
    return text.replace("|", "\\|").replace("\n", " ")


def _date(value):
    return (value or "")[:10]


def to_json(reports, info, extra, show_all=False):
    shown = [r for r in reports if show_all or r.verdict != "failing"]
    return json.dumps({"version": __version__, "scan": info, "summary": extra,
                       "tests": [r.to_dict() for r in shown]}, indent=2)


def to_markdown(reports, info, extra, show_all=False):
    flaky = [r for r in reports if r.verdict == "flaky"]
    suspect = [r for r in reports if r.verdict == "suspect"]
    failing = [r for r in reports if r.verdict == "failing"]
    minutes = sum(r.minutes for r in flaky)
    out = ["# %s for `%s`" % (ISSUE_TITLE, info["repo"]), ""]
    scope = "%d completed workflow runs from %s to %s" % (info["runs"], _date(info["first_run"]), _date(info["last_run"]))
    if info.get("workflow_filter"):
        scope += ", workflows matching `%s`" % info["workflow_filter"]
    if info.get("branch_filter"):
        scope += ", branch `%s`" % info["branch_filter"]
    out.append("Scanned %s." % scope)
    out.append("")
    if flaky:
        out.append("**%d flaky test%s** found, involved in %.0f CI minutes of failed jobs."
                   % (len(flaky), "" if len(flaky) == 1 else "s", minutes))
    else:
        out.append("**No confirmed flaky tests** in this window.")
    if suspect:
        out.append("%d more test%s look suspicious (failing on several branches) but aren't proven flaky."
                   % (len(suspect), "" if len(suspect) == 1 else "s"))
    out.append("")

    rows = flaky + suspect + (failing if show_all else [])
    if rows:
        out.append("| # | Test | Verdict | Evidence | Failures | Commits | CI min | Last seen |")
        out.append("|---|------|---------|----------|---------:|--------:|-------:|-----------|")
        for i, r in enumerate(rows, 1):
            out.append("| %d | `%s` | %s | %s | %d | %d | %.0f | %s |" % (
                i, _md_escape(_short(r.nodeid)), r.verdict, _md_escape(r.evidence()), len(r.failures),
                len(r.shas), r.minutes, _date(r.last_seen)))
        out.append("")
        out.append("<details><summary>Failure messages and links</summary>")
        out.append("")
        for i, r in enumerate(rows, 1):
            out.append("**%d. `%s`**" % (i, _md_escape(r.nodeid)))
            for msg in r.messages[:3]:
                out.append("- `%s`" % _md_escape(msg[:200]))
            links = ["[job](%s)" % j.url for j, _ in r.failures[:5] if j.url]
            links += ["[rerun](%s)" % j.url for j in r.rerun_jobs[:3] if j.url]
            if links:
                out.append("- Failed jobs: " + ", ".join(links))
            out.append("- Reproduce locally: `flake-doctor run '%s'`" % r.nodeid)
            out.append("")
        out.append("</details>")
        out.append("")

    flaky_jobs = extra.get("flaky_jobs") or []
    if flaky_jobs:
        out.append("### Jobs that failed and then passed on the same commit")
        out.append("")
        out.append("| Job | Failures | Passed on same commit |")
        out.append("|-----|---------:|----------------------:|")
        for fj in flaky_jobs[:10]:
            out.append("| %s | %d | %d |" % (_md_escape(fj["job"]), fj["failures"], fj["same_commit_passes"]))
        out.append("")
    infra = extra.get("failed_jobs_without_test_failures") or {}
    notes = []
    if failing and not show_all:
        notes.append("%d test%s failed only on a single branch or commit; those look like real failures and "
                     "are hidden (use `--all` to list them)." % (len(failing), "" if len(failing) == 1 else "s"))
    if infra:
        notes.append("%d failed job%s had no pytest failures in the log (setup, lint, timeouts or other "
                     "tools): %s." % (sum(infra.values()), "" if sum(infra.values()) == 1 else "s",
                                      ", ".join("%s ×%d" % (k, v) for k, v in list(infra.items())[:5])))
    if not info.get("logs_read", True):
        notes.append("Job logs weren't read because no GitHub token was found, so only job-level results are "
                     "shown. Set GITHUB_TOKEN to see which tests flaked.")
    if info.get("hit_max_runs"):
        notes.append("The scan stopped at --max-runs, so it covers less than the requested --days. Raise "
                     "--max-runs or use --workflow to scan only your test workflows.")
    if info.get("logs_skipped_by_budget"):
        notes.append("%d job logs weren't read to stay within --max-logs (matrix siblings of jobs already read, "
                     "or older failures). Raise --max-logs for a fuller scan." % info["logs_skipped_by_budget"])
    if info.get("logs_read", True) and extra.get("failed_jobs_with_missing_logs"):
        notes.append("%d failed job logs were unavailable (expired or not accessible)."
                     % extra["failed_jobs_with_missing_logs"])
    if notes:
        out.append("### Notes")
        out.extend("- " + n for n in notes)
        out.append("")
    out.append("<sub>Generated by [flake-doctor](%s) %s. A test is **flaky** when the same job passed on the "
               "same commit after it failed, or pytest-rerunfailures had to retry it. **Suspect** means it "
               "failed on 3+ commits across 2+ branches.</sub>" % (HOME, __version__))
    return "\n".join(out) + "\n"


def to_text(reports, info, extra, show_all=False):
    flaky = [r for r in reports if r.verdict == "flaky"]
    suspect = [r for r in reports if r.verdict == "suspect"]
    failing = [r for r in reports if r.verdict == "failing"]
    lines = ["%s: %d runs, %s to %s" % (info["repo"], info["runs"], _date(info["first_run"]), _date(info["last_run"])),
             "flaky: %d   suspect: %d   real failures (hidden): %d   failed jobs: %d"
             % (len(flaky), len(suspect), len(failing), extra.get("failed_jobs", 0)), ""]
    rows = flaky + suspect + (failing if show_all else [])
    for r in rows:
        lines.append("[%s] %s" % (r.verdict.upper(), r.nodeid))
        lines.append("    %s; %d failures, %.0f CI min, last %s" % (r.evidence(), len(r.failures), r.minutes, _date(r.last_seen)))
        if r.messages:
            lines.append("    %s" % r.messages[0][:160])
    if not rows:
        lines.append("No flaky or suspect tests found." if info.get("logs_read", True) else
                     "Test-level results need job logs, which need a GitHub token (set GITHUB_TOKEN).")
    flaky_jobs = extra.get("flaky_jobs") or []
    if flaky_jobs:
        lines.append("")
        lines.append("jobs that failed and then passed on the same commit:")
        for fj in flaky_jobs[:10]:
            lines.append("  %3d/%-3d %s" % (fj["same_commit_passes"], fj["failures"], fj["job"]))
    infra = extra.get("failed_jobs_without_test_failures") or {}
    if infra:
        lines.append("")
        lines.append("failed jobs without pytest failures: %s" % ", ".join("%s ×%d" % kv for kv in list(infra.items())[:5]))
    if info.get("hit_max_runs"):
        lines.append("")
        lines.append("note: stopped at --max-runs before reaching --days; raise it or use --workflow.")
    lines.append("")
    lines.append("(%d GitHub API requests)" % info.get("api_requests", 0))
    return "\n".join(lines) + "\n"


def upsert_issue(gh, repo, body):
    """Create the report issue, or update the open one with the same label. Returns its URL."""
    issues = gh.get_json("/repos/%s/issues" % repo, {"state": "open", "labels": ISSUE_LABEL, "per_page": 10})
    existing = [i for i in issues if i.get("title") == ISSUE_TITLE and "pull_request" not in i]
    if existing:
        issue = gh.request("PATCH", "/repos/%s/issues/%d" % (repo, existing[0]["number"]), {"body": body})
    else:
        issue = gh.request("POST", "/repos/%s/issues" % repo,
                           {"title": ISSUE_TITLE, "body": body, "labels": [ISSUE_LABEL]})
    return issue["html_url"]
