"""Command-line entry point: `flake-doctor <command> ...`."""

import argparse
import os
import re
import sys

from . import __version__

COMMANDS = {
    "scan": "find flaky tests in a repo's GitHub Actions history",
    "run": "rerun one test many times (alone and in shuffled suite order) and get a verdict",
    "polluter": "find the test that breaks another test when it runs first",
    "hints": "point at code patterns that commonly cause flakiness",
}


def _repo_from_git():
    """owner/repo from GITHUB_REPOSITORY or the origin remote, if it's on github.com."""
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    import subprocess
    proc = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", proc.stdout.strip())
    return m.group(1) if m else None


def scan_main(argv):
    from .github import GitHub, GitHubError, default_cache_dir, find_token
    from .report import to_json, to_markdown, to_text, upsert_issue
    from .scan import analyze, collect

    parser = argparse.ArgumentParser(
        prog="flake-doctor scan",
        description="Find flaky pytest tests in a repository's GitHub Actions history.",
        epilog="Needs a GitHub token to read job logs: GITHUB_TOKEN or GH_TOKEN, `gh auth login`, "
               "or a token in ~/.config/flake-doctor/token. Read-only access to the repo is enough "
               "(fine-grained: Actions read; Issues write only for --issue).")
    parser.add_argument("repo", nargs="?", help="owner/repo (default: GITHUB_REPOSITORY or the git origin)")
    parser.add_argument("--days", type=int, default=30, help="how far back to look (default 30)")
    parser.add_argument("--workflow", help="only workflows whose name or file contains this text")
    parser.add_argument("--branch", help="only runs on this branch")
    parser.add_argument("--max-runs", type=int, default=5000, help="cap on workflow runs to scan (default 5000)")
    parser.add_argument("--max-logs", type=int, default=400,
                        help="cap on job logs to download; logs that can prove flakiness are read first (default 400)")
    parser.add_argument("--scan-passing", action="store_true",
                        help="also read logs of passing jobs, to catch tests that pytest-rerunfailures retried "
                             "(more API requests)")
    parser.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    parser.add_argument("--output", "-o", help="write the report to this file instead of stdout")
    parser.add_argument("--all", action="store_true", help="also list tests that look like real (non-flaky) failures")
    parser.add_argument("--issue", action="store_true", help="create or update a 'Flaky test report' issue in the repo")
    parser.add_argument("--fail-on-flaky", action="store_true", help="exit with status 1 if any flaky test is found")
    parser.add_argument("--no-cache", action="store_true", help="don't use the on-disk cache of jobs and logs")
    parser.add_argument("--quiet", "-q", action="store_true", help="no progress output")
    args = parser.parse_args(argv)

    repo = args.repo or _repo_from_git()
    if not repo or "/" not in repo:
        parser.error("give the repository as owner/repo")
    token = find_token()
    if not token:
        print("warning: no GitHub token found; job logs can't be read without one and the API allows only "
              "60 requests/hour. See `flake-doctor scan --help`.", file=sys.stderr)
    gh = GitHub(token=token, cache_dir=None if args.no_cache else default_cache_dir())
    try:
        jobs, info = collect(gh, repo, days=args.days, workflow=args.workflow, branch=args.branch,
                             max_runs=args.max_runs, scan_passing=args.scan_passing, quiet=args.quiet,
                             max_logs=args.max_logs)
        reports, extra = analyze(jobs)
        info["api_requests"] = gh.requests
        render = {"text": to_text, "markdown": to_markdown, "json": to_json}[args.format]
        output = render(reports, info, extra, show_all=args.all)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
        else:
            sys.stdout.write(output)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a") as f:
                f.write(to_markdown(reports, info, extra, show_all=args.all))
        if args.issue:
            url = upsert_issue(gh, repo, to_markdown(reports, info, extra, show_all=args.all))
            print("report issue: %s" % url, file=sys.stderr)
    except GitHubError as err:
        print("\nerror: %s" % err, file=sys.stderr)
        return 2
    if args.fail_on_flaky and any(r.verdict == "flaky" for r in reports):
        return 1
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: flake-doctor <command> [options]\n\ncommands:")
        for name, desc in COMMANDS.items():
            print("  %-9s %s" % (name, desc))
        print("\nRun `flake-doctor <command> --help` for details.")
        sys.exit(0)
    if argv[0] in ("-V", "--version"):
        print("flake-doctor %s" % __version__)
        sys.exit(0)
    command, rest = argv[0], argv[1:]
    if command == "scan":
        sys.exit(scan_main(rest))
    if command == "run":
        from .rerun import main as run_main
        sys.exit(run_main(rest))
    if command == "polluter":
        from .polluter import main as polluter_main
        sys.exit(polluter_main(rest))
    if command == "hints":
        from .hints import main as hints_main
        sys.exit(hints_main(rest))
    print("flake-doctor: unknown command %r (try --help)" % command, file=sys.stderr)
    sys.exit(2)
