"""Find the test that breaks another test when it runs first (the "polluter").

Give it the victim test and a failing order: either a shuffle seed reported by
`flake-doctor run` (with the same --scope), or a file listing node ids in run order.
It confirms the order fails, confirms the victim passes alone, then bisects the
tests that ran before the victim down to the smallest set that still breaks it.

Usage:
  flake-doctor polluter tests/test_b.py::test_victim --seed 4821 --scope tests/
  flake-doctor polluter tests/test_b.py::test_victim --order-file failing_order.txt
"""

import argparse
import sys
import time
from pathlib import Path

from .runner import PytestRunner, files_for


class Prober:
    def __init__(self, runner, victim, tries, timeout):
        self.runner = runner
        self.victim = victim
        self.tries = tries
        self.timeout = timeout
        self.calls = 0

    def fails(self, prefix):
        """True if the victim fails in any of `tries` runs of prefix + [victim]."""
        order = list(prefix) + [self.victim]
        for _ in range(self.tries):
            self.calls += 1
            report = self.runner.run(files_for(order), targets=[self.victim], order=order, timeout=self.timeout)
            result = report["results"].get(self.victim)
            if result is None:
                raise SystemExit("flake-doctor: victim did not run.\n--- pytest output ---\n%s" % report["output"])
            if result["outcome"] == "failed":
                sys.stderr.write("F")
                sys.stderr.flush()
                return True
            sys.stderr.write(".")
            sys.stderr.flush()
        return False


def bisect(prober, candidates):
    """Shrink `candidates` (kept in run order) while the victim keeps failing."""
    while len(candidates) > 1:
        half = len(candidates) // 2
        first, second = candidates[:half], candidates[half:]
        if prober.fails(first):
            candidates = first
        elif prober.fails(second):
            candidates = second
        else:
            break  # the failure needs tests from both halves together
    if len(candidates) > 1:
        # Try removing one test at a time to get a minimal set.
        i = 0
        while i < len(candidates) and len(candidates) > 1:
            trial = candidates[:i] + candidates[i + 1:]
            if prober.fails(trial):
                candidates = trial
            else:
                i += 1
    return candidates


def main(argv=None):
    parser = argparse.ArgumentParser(prog="flake-doctor polluter", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("victim", help="node id of the test that fails depending on order")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--seed", type=int, help="a failing shuffle seed from `flake-doctor run`")
    group.add_argument("--order-file", help="file with node ids in the order they ran (one per line)")
    parser.add_argument("--scope", nargs="+", help="same --scope used with `flake-doctor run` (default: victim's directory)")
    parser.add_argument("--root", default=".")
    parser.add_argument("--python")
    parser.add_argument("--tries", type=int, default=3, help="runs per check; a check fails if any run fails (default 3)")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--pytest-args", default="")
    args = parser.parse_args(argv)

    runner = PytestRunner(args.root, args.python, args.pytest_args.split())
    victim = args.victim
    started = time.time()

    if args.order_file:
        order = [l.strip() for l in Path(args.order_file).read_text().splitlines() if l.strip()]
    else:
        scope = args.scope or [str(Path(victim.split("::", 1)[0]).parent) or "."]
        report = runner.run(scope, targets=[victim], seed=args.seed, timeout=args.timeout)
        order = report["order"]
    if victim not in order:
        raise SystemExit("flake-doctor: %s is not in that order. Check --scope matches the `flake-doctor run` call." % victim)
    before = order[: order.index(victim)]
    print("flake-doctor: finding what breaks %s" % victim)
    print("tests before it in the failing order: %d" % len(before))

    prober = Prober(runner, victim, args.tries, args.timeout)
    sys.stderr.write("checking failing order: ")
    if not prober.fails(before):
        sys.stderr.write("\n")
        print("\nCould not reproduce the failure with this order in %d tries. It may be intrinsically flaky "
              "(run `flake-doctor run --mode isolated`) or need more --tries." % args.tries)
        return
    sys.stderr.write("\nchecking victim alone:  ")
    if prober.fails([]):
        sys.stderr.write("\n")
        print("\nThe victim also fails when run alone, so it is not purely order-dependent. "
              "Treat it as intrinsically flaky first (`flake-doctor run --mode isolated`).")
        return
    sys.stderr.write("\nbisecting:              ")
    culprits = bisect(prober, before)
    sys.stderr.write("\n")

    print()
    if len(culprits) == 1:
        print("Polluter: %s" % culprits[0])
        print("  Running it before the victim makes the victim fail; the victim passes alone.")
    else:
        print("The failure needs these tests together (in this order):")
        for c in culprits:
            print("  %s" % c)
    print("Reproduce with:\n  pytest %s -p no:randomly" % " ".join(culprits + [victim]))
    print("  (pytest runs node ids in the order given on the command line)")
    print("\n(%d pytest runs, %.1fs)" % (prober.calls, time.time() - started))

