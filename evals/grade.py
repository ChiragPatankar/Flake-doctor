#!/usr/bin/env python3
"""Programmatic checks for flake-doctor eval runs.

Usage:
  python grade.py <iteration-dir>
      writes grading.json with the scripted checks into every <eval>/<config>/run-N/
      (each run's project is compared with the original example named in evals.json)
  python grade.py --merge <iteration-dir> <judgments.json>
      appends reviewer judgments ({"eval-1-x/with_skill/run-1": [{text, passed, evidence}]})
      and recomputes each summary
"""

import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
# flake-doctor run, from this checkout (works without installing the package)
FLAKE_DOCTOR = [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from flake_doctor.cli import main; main()" % str(REPO / "src")]

# eval id -> (target test, flaky_run options that would catch the original flake)
TARGETS = {
    1: ("tests/test_reports.py::test_report_timestamp", ["--runs", "60", "--shuffle-runs", "20"]),
    2: ("tests/test_pricing.py::test_default_currency", ["--runs", "60", "--shuffle-runs", "20"]),
    3: ("tests/test_coupons.py::test_codes_are_unique", ["--runs", "60", "--shuffle-runs", "20"]),
    4: ("tests/test_cart.py::test_cart_total", None),
    5: ("tests/test_checkout.py::test_order_total[standard]", ["--runs", "20", "--shuffle-runs", "40"]),
    6: ("tests/test_invoices.py::test_invoice_total", ["--mode", "isolated", "--runs", "200"]),
    7: ("tests/test_ratelimit.py::test_blocks_fourth_call", ["--mode", "isolated", "--runs", "60", "--stress"]),
    8: ("tests/test_refunds.py::test_refund_card_order", ["--runs", "20", "--shuffle-runs", "10"]),
}
HIDING = re.compile(r"mark\.flaky|reruns?\b|@retry|mark\.skip|mark\.xfail|pytest\.skip|PYTHONHASHSEED|random\.seed\(|pytest-order|mark\.order", re.I)


def source_files(root):
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts}


def diff(orig, new):
    a, b = source_files(orig), source_files(new)
    out = []
    for name in sorted(set(a) | set(b)):
        if a.get(name) != b.get(name):
            out += difflib.unified_diff((a.get(name) or "").splitlines(), (b.get(name) or "").splitlines(), "a/" + name, "b/" + name, lineterm="")
    return out


def count_tests(root):
    return len(re.findall(r"^\s*def test_", "\n".join(t for n, t in source_files(root).items() if n.startswith("tests/")), re.M))


def flaky_numbers(project, target, options):
    out = project.parent / "outputs" / "_grader_rerun.json"
    subprocess.run(FLAKE_DOCTOR + ["run", target, "--root", str(project), "--scope", "tests", "--json", str(out)] + options,
                   capture_output=True, text=True, timeout=1800)
    data = json.loads(out.read_text())
    out.unlink()
    parts = []
    failed = 0
    for mode in ("isolated", "shuffle"):
        if data.get(mode):
            failed += data[mode]["failed"]
            parts.append("%s %d/%d failed" % (mode, data[mode]["failed"], data[mode]["runs"]))
    return failed, ", ".join(parts) + (" (CPU stress)" if "--stress" in options else "")


def py(project, code, env=None):
    """Run `code` in a fresh interpreter with the project importable."""
    full = "import sys; sys.path.insert(0, %r)\n%s" % (str(project), code)
    proc = subprocess.run([sys.executable, "-c", full], capture_output=True, text=True, timeout=120,
                          env=dict(os.environ, **(env or {})))
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def product_check(eval_id, project):
    """(assertion text, passed, evidence) for checks on the product code itself, or None."""
    if eval_id == 3:
        rc, out = py(project, "from shop.coupons import generate_codes\n"
                              "print(sum(len(set(c)) != len(c) for c in (generate_codes(10) for _ in range(2000))))")
        return "generate_codes itself guarantees distinct codes (2000 calls of n=10)", rc == 0 and out == "0", "calls with duplicates: " + out[-200:]
    if eval_id == 5:
        rc, out = py(project, "from shop.catalog import get_product\nfrom shop.catalog_data import CATALOG\n"
                              "from shop.promotions import flash_sale_price\n"
                              "before = CATALOG['WID-100S']['price']\n"
                              "sale = flash_sale_price('WID-100S', 20)\n"
                              "after = get_product('WID-100S')['price']\n"
                              "print(sale == round(before * 0.8, 2), after == before, sale, before, after)")
        return ("Product bug fixed: a flash sale no longer changes the cached catalog price",
                rc == 0 and out.startswith("True True"), "sale ok, catalog unchanged, sale, list, after: " + out[-200:])
    if eval_id == 6:
        code = ("from shop.invoices import invoice_total\n"
                "v = invoice_total({'WID-100': 1, 'GAD-220': 2, 'BOL-315': 1, 'NUT-404': 2, 'SPR-512': 3, 'CLP-618': 3})\n"
                "print(repr(v), abs(float(v) - 450.87) < 1e-9)")
        outs = set()
        ok = True
        for seed in range(40):
            rc, out = py(project, code, {"PYTHONHASHSEED": str(seed)})
            outs.add(out)
            ok = ok and rc == 0 and out.endswith("True")
        return ("Product fix: invoice_total is identical across 40 hash seeds and exact to the cent",
                ok and len(outs) == 1, "distinct results: %s" % sorted(outs)[:3])
    if eval_id == 8:
        rc, out = py(project, "from shop.refunds import refund_order\n"
                              "print(refund_order({'method': 'card', 'amount': 42.0, 'age_days': 3}))")
        return ("Product bug fixed: refund_order finds the card provider without the test-only import",
                rc == 0 and "refunded" in out, out[-200:])
    return None


def grade_run(run_dir, eval_id, orig):
    project = run_dir / "project"
    lines = diff(orig, project)
    added = [l[1:] for l in lines if l.startswith("+") and not l.startswith("+++")]
    changed_files = sorted({l[6:] for l in lines if l.startswith("+++ b/")})
    checks = []

    def add(text, passed, evidence):
        checks.append({"text": text, "passed": bool(passed), "evidence": evidence})

    target, options = TARGETS[eval_id]
    if options:
        failed, evidence = flaky_numbers(project, target, options)
        add("Target test no longer flaky (%s)" % " ".join(options), failed == 0, evidence)
        code_lines = [l.split("#", 1)[0] for l in added]  # comments may explain the cause; only code counts
        hiding = [l.strip() for l in code_lines if HIDING.search(l)]
        add("No hiding fix (retries, skip/xfail, pinned seeds, forced order)", not hiding,
            "; ".join(hiding[:3]) or "none found in added lines")
        add("No tests deleted", count_tests(project) >= count_tests(orig),
            "%d test functions (originally %d)" % (count_tests(project), count_tests(orig)))
    else:
        add("No code changes for a test that isn't reproducibly flaky", not changed_files,
            "changed: %s" % (", ".join(changed_files) or "nothing"))
    if eval_id == 2:
        add("Polluter test_admin.py cleaned up (file changed)", "tests/test_admin.py" in changed_files,
            "changed: %s" % (", ".join(changed_files) or "nothing"))
    check = product_check(eval_id, project)
    if check:
        add(*check)

    (run_dir / "outputs" / "diff.patch").write_text("\n".join(lines) + "\n")
    return checks


def write_grading(run_dir, checks):
    passed = sum(c["passed"] for c in checks)
    summary = {"passed": passed, "failed": len(checks) - passed, "total": len(checks),
               "pass_rate": round(passed / len(checks), 2) if checks else 0.0}
    (run_dir / "grading.json").write_text(json.dumps({"expectations": checks, "summary": summary}, indent=2))


def main():
    if sys.argv[1] == "--merge":
        iteration, judgments = Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text())
        for rel, extra in judgments.items():
            run_dir = iteration / rel
            checks = json.loads((run_dir / "grading.json").read_text())["expectations"]
            checks = [c for c in checks if c["text"] not in {e["text"] for e in extra}] + extra
            write_grading(run_dir, checks)
        return
    iteration = Path(sys.argv[1])
    examples = {e["id"]: REPO / e["files"][0] for e in json.loads((HERE / "evals.json").read_text())["evals"]}
    for run_dir in sorted(iteration.glob("eval-*/*/run-*")):
        eval_id = int(run_dir.parent.parent.name.split("-")[1])
        checks = grade_run(run_dir, eval_id, examples[eval_id])
        write_grading(run_dir, checks)
        print("%s: %s" % (run_dir.relative_to(iteration),
                          ", ".join(("PASS" if c["passed"] else "FAIL") for c in checks)))


if __name__ == "__main__":
    main()
