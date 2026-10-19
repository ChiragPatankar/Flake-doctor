"""Measure whether a pytest test is flaky, and in what way.

Runs the test many times in two ways:
  isolated  the test alone, each run in a fresh process (catches timing, randomness,
            clock, hash-ordering and network flakiness)
  shuffle   the test together with its neighbours in a random order, each run with a
            new seed (catches tests that depend on, or are broken by, other tests)

Then prints failure rates, groups failures by message, lists tests that correlate
with failure when they run first, and gives a verdict with the next step.

Usage:
  flake-doctor run tests/test_api.py::test_login
  flake-doctor run tests/test_api.py::test_login --runs 50 --stress
  flake-doctor run tests/test_api.py::test_login --mode shuffle --scope tests/
  flake-doctor run tests/test_api.py::test_login --json before.json
"""

import argparse
import json
import math
import random
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .runner import CpuStress, PytestRunner, signature


def runs_for_confidence(rate, confidence=0.95):
    """Clean runs needed to be `confidence` sure a test failing at `rate` is fixed."""
    if rate <= 0:
        return None
    if rate >= 1:
        return 1
    return math.ceil(math.log(1 - confidence) / math.log(1 - rate))


def _progress(outcome):
    sys.stderr.write("F" if outcome == "failed" else ".")
    sys.stderr.flush()


def _summarise(runs):
    failed = [r for r in runs if r["outcome"] == "failed"]
    passed = [r for r in runs if r["outcome"] == "passed"]
    sigs = Counter(signature(r.get("message")) for r in failed)
    examples = {}
    for r in failed:
        examples.setdefault(signature(r.get("message")), (r.get("message") or "").strip().splitlines()[0][:200] if r.get("message") else "<no message>")
    phases = Counter(r.get("phase") for r in failed)

    def avg(rows):
        return round(sum(r.get("duration", 0.0) for r in rows) / len(rows), 3) if rows else None

    sample = next((r for r in failed), None)
    return {
        "runs": len(runs),
        "failed": len(failed),
        "skipped": sum(1 for r in runs if r["outcome"] == "skipped"),
        "failure_rate": round(len(failed) / len(runs), 3) if runs else 0.0,
        "signatures": [{"count": c, "signature": s, "example": examples[s]} for s, c in sigs.most_common()],
        "failed_phases": dict(phases),
        "avg_duration_passed": avg(passed),
        "avg_duration_failed": avg(failed),
        "sample_failure": {"message": sample.get("message"), "traceback": sample.get("traceback")} if sample else None,
    }


def run_isolated(runner, target, runs, workers, timeout):
    def one(_):
        report = runner.run([target], targets=[target], timeout=timeout)
        result = report["results"].get(target)
        if result is None:
            return {"outcome": "error", "output": report["output"]}
        _progress(result["outcome"])
        return result

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, range(runs)))
    errors = [r for r in results if r["outcome"] == "error"]
    if errors:
        sys.stderr.write("\n")
        raise SystemExit(
            "flake-doctor: pytest did not run %s. Check the node id and rootdir.\n--- pytest output ---\n%s"
            % (target, errors[0]["output"])
        )
    return results


def run_shuffled(runner, target, scope, runs, timeout):
    args = list(scope)
    target_file = target.split("::", 1)[0]
    if not any(target_file == s or target_file.startswith(s.rstrip("/") + "/") for s in scope):
        args.append(target_file)
    rows = []
    rng = random.Random()
    for _ in range(runs):
        seed = rng.randrange(1, 10**6)
        report = runner.run(args, targets=[target], seed=seed, timeout=timeout)
        result = report["results"].get(target)
        if result is None:
            sys.stderr.write("\n")
            raise SystemExit(
                "flake-doctor: %s was not run in the shuffled suite (scope: %s).\n--- pytest output ---\n%s"
                % (target, " ".join(args), report["output"])
            )
        order = report["order"]
        before = order[: order.index(target)] if target in order else []
        rows.append(dict(result, seed=seed, before=before, order=order))
        _progress(result["outcome"])
    return rows


def suspects(rows, target):
    """Rank other tests by how much running them before the target changes its failure rate."""
    tests = {t for r in rows for t in r["order"] if t != target}
    ranked = []
    for test in tests:
        before = [r for r in rows if test in r["before"]]
        after = [r for r in rows if test not in r["before"]]
        if len(before) < 2 or len(after) < 2:
            continue
        fb = sum(r["outcome"] == "failed" for r in before)
        fa = sum(r["outcome"] == "failed" for r in after)
        lift = fb / len(before) - fa / len(after)
        ranked.append({
            "test": test,
            "lift": round(lift, 3),
            "failed_when_before": "%d/%d" % (fb, len(before)),
            "failed_when_after": "%d/%d" % (fa, len(after)),
        })
    ranked.sort(key=lambda s: abs(s["lift"]), reverse=True)
    # A lift of +/-1 means the outcome flips exactly with this test's position: that is the
    # culprit, and weaker correlations are just tests that tend to sit near it in the order.
    exact = [s for s in ranked if abs(s["lift"]) == 1.0]
    if len(exact) > 1:
        # Tests' positions are correlated through the target's own position, so a bystander can match
        # the failures perfectly by chance. This data can't tell them apart; bisection can.
        for s in exact:
            s["tied"] = True
    return exact[:5] if exact else [s for s in ranked if abs(s["lift"]) >= 0.5][:5]


def verdict(iso, shuf):
    iso_rate = iso["failure_rate"] if iso else None
    shuf_rate = shuf["failure_rate"] if shuf else None
    rates = [r for r in (iso_rate, shuf_rate) if r is not None]
    if rates and all(r == 1.0 for r in rates):
        return "ALWAYS-FAILS", "Fails on every run. This is a real, deterministic failure, not flakiness: debug it normally."
    if iso_rate == 1.0 and shuf_rate is not None and shuf_rate < 1.0:
        return "NEEDS-OTHER-TESTS", (
            "Fails every time alone but sometimes passes in the suite: it depends on state that another test "
            "sets up. See the suspect list (negative lift = the test it relies on)."
        )
    if iso_rate:
        return "FLAKY-INTRINSIC", (
            "Fails even when run alone, so the cause is inside the test or the code it calls "
            "(timing, randomness, clock, hash ordering, network, concurrency). See https://github.com/ChiragPatankar/flake-doctor/blob/main/plugins/flake-doctor/skills/flake-doctor/references/causes.md"
        )
    if shuf_rate:
        return "ORDER-DEPENDENT", (
            "Passes alone but fails in some suite orders: another test leaks state into it. "
            "Confirm the culprit with `flake-doctor polluter` using a failing seed."
        )
    total = sum(s["runs"] for s in (iso, shuf) if s)
    return "NOT-REPRODUCED", (
        "No failures in %d runs (so a failure rate above ~%.0f%% is unlikely, by the rule of three). "
        "Try --stress, more --runs, a wider --scope, or the CI environment's settings before changing code."
        % (total, 300.0 / total if total else 100)
    )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="flake-doctor run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("test", help="pytest node id, e.g. tests/test_api.py::test_login")
    parser.add_argument("--root", default=".", help="project root to run pytest from (default: .)")
    parser.add_argument("--python", help="interpreter with the project's deps (default: auto-detect .venv)")
    parser.add_argument("--mode", choices=["isolated", "shuffle", "both"], default="both")
    parser.add_argument("--runs", type=int, default=30, help="isolated runs (default 30)")
    parser.add_argument("--shuffle-runs", type=int, default=20, help="shuffled-suite runs (default 20)")
    parser.add_argument("--scope", nargs="+", help="paths to shuffle with the test (default: the test's directory)")
    parser.add_argument("--stress", action="store_true", help="load every CPU core during isolated runs, like a busy CI box")
    parser.add_argument("--workers", type=int, default=1, help="parallel isolated runs (faster, but may add contention)")
    parser.add_argument("--timeout", type=int, default=300, help="seconds per pytest run (default 300)")
    parser.add_argument("--pytest-args", default="", help="extra args passed to pytest, as one string")
    parser.add_argument("--json", help="also write the full results to this JSON file")
    args = parser.parse_args(argv)

    runner = PytestRunner(args.root, args.python, args.pytest_args.split())
    target = args.test
    scope = args.scope or [str(Path(target.split("::", 1)[0]).parent) or "."]
    started = time.time()

    print("flake-doctor: %s" % target)
    print("python: %s" % runner.python)

    iso = shuf = None
    iso_rows = shuf_rows = []
    if args.mode in ("isolated", "both"):
        sys.stderr.write("isolated: ")
        if args.stress:
            with CpuStress():
                iso_rows = run_isolated(runner, target, args.runs, args.workers, args.timeout)
        else:
            iso_rows = run_isolated(runner, target, args.runs, args.workers, args.timeout)
        sys.stderr.write("\n")
        iso = _summarise(iso_rows)
    if args.mode in ("shuffle", "both"):
        sys.stderr.write("shuffle:  ")
        shuf_rows = run_shuffled(runner, target, scope, args.shuffle_runs, args.timeout)
        sys.stderr.write("\n")
        shuf = _summarise(shuf_rows)
        shuf["scope"] = scope
        shuf["failing_seeds"] = [r["seed"] for r in shuf_rows if r["outcome"] == "failed"][:10]
        shuf["passing_seeds"] = [r["seed"] for r in shuf_rows if r["outcome"] == "passed"][:3]
        # Suspects only mean something when the test's outcome is decided by its neighbours.
        if iso is None or iso["failure_rate"] in (0.0, 1.0):
            shuf["suspects"] = suspects(shuf_rows, target)

    for label, s in (("Isolated runs" + (" (CPU stress)" if args.stress else ""), iso), ("Shuffled-suite runs", shuf)):
        if not s:
            continue
        print("\n%s: %d/%d failed (%.0f%%)" % (label, s["failed"], s["runs"], 100 * s["failure_rate"]))
        if s.get("scope"):
            print("  scope: %s" % " ".join(s["scope"]))
        for sig in s["signatures"][:5]:
            print("  %3dx  %s" % (sig["count"], sig["example"]))
        if s["failed_phases"] and set(s["failed_phases"]) != {"call"}:
            print("  failed in phase: %s" % s["failed_phases"])
        if s["avg_duration_failed"] is not None and s["avg_duration_passed"] is not None:
            print("  avg duration: passing %.3fs, failing %.3fs" % (s["avg_duration_passed"], s["avg_duration_failed"]))
        if s.get("suspects"):
            print("  tests that change the outcome when they run before it:")
            if any(sp.get("tied") for sp in s["suspects"]):
                print("    (tied: each of these matches the failures perfectly, so this data can't tell them apart;"
                      " `flake-doctor polluter` will)")
            for sp in s["suspects"]:
                print("    %+.2f  %s  (failed %s when before, %s when after)"
                      % (sp["lift"], sp["test"], sp["failed_when_before"], sp["failed_when_after"]))
        if s.get("failing_seeds"):
            print("  failing seeds: %s" % ", ".join(map(str, s["failing_seeds"][:5])))

    name, explanation = verdict(iso, shuf)
    print("\nVerdict: %s\n  %s" % (name, explanation))
    worst = max((s["failure_rate"] for s in (iso, shuf) if s), default=0)
    needed = runs_for_confidence(worst) if 0 < worst < 1 else None
    if needed:
        flag = "--runs" if iso and iso["failure_rate"] == worst else "--shuffle-runs"
        print("  To verify a fix with 95%% confidence it needs ~%d clean runs; use at least 3x that "
              "(e.g. %s %d) and run both modes again." % (needed, flag, max(30, needed * 3)))
    if name == "ORDER-DEPENDENT" and shuf.get("failing_seeds"):
        print("  Next: flake-doctor polluter '%s' --seed %d --scope %s"
              % (target, shuf["failing_seeds"][0], " ".join(scope)))
    if iso and iso.get("sample_failure"):
        print("\nSample failure (isolated):\n%s" % (iso["sample_failure"]["traceback"] or iso["sample_failure"]["message"])[-1500:])
    elif shuf and shuf.get("sample_failure"):
        print("\nSample failure (shuffled):\n%s" % (shuf["sample_failure"]["traceback"] or shuf["sample_failure"]["message"])[-1500:])
    print("\n(%.1fs)" % (time.time() - started))

    if args.json:
        out = {"test": target, "python": runner.python, "isolated": iso, "shuffle": shuf,
               "verdict": name, "runs_for_95pct_confidence": needed}
        Path(args.json).write_text(json.dumps(out, indent=2))

