"""End-to-end checks of the local tools against examples/flaky-zoo (real pytest subprocesses)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ZOO = Path(__file__).resolve().parent.parent / "examples" / "flaky-zoo"


def cli(*args):
    proc = subprocess.run([sys.executable, "-m", "flake_doctor"] + list(args), capture_output=True, text=True,
                          cwd=ZOO, timeout=600)
    return proc.returncode, proc.stdout, proc.stderr


def run_json(tmp_path, target, *args):
    out = tmp_path / "result.json"
    code, stdout, stderr = cli("run", target, "--python", sys.executable, "--json", str(out), *args)
    assert code == 0, stderr + stdout
    return json.loads(out.read_text())


def test_intrinsic_flake_is_detected(tmp_path):
    # ~50% failure rate: 16 isolated runs all passing would happen with p ~ 0.00002
    data = run_json(tmp_path, "tests/test_exports.py::test_export_finishes", "--mode", "isolated", "--runs", "16")
    assert data["verdict"] == "FLAKY-INTRINSIC"
    assert 0 < data["isolated"]["failed"] < 16
    assert data["runs_for_95pct_confidence"] >= 1


def test_order_dependent_flake_and_polluter(tmp_path):
    target = "tests/test_pricing.py::test_default_currency"
    data = run_json(tmp_path, target, "--runs", "4", "--shuffle-runs", "16", "--scope", "tests")
    summary = json.dumps({k: data[k] for k in ("verdict", "isolated", "shuffle")}, indent=1)[:3000]
    assert data["verdict"] == "ORDER-DEPENDENT", summary
    assert data["isolated"]["failed"] == 0, summary
    # The polluter always separates failures perfectly. A bystander can tie with it by chance (their
    # positions are correlated through the victim's), in which case all tied tests are flagged as such.
    perfect = [s for s in data["shuffle"]["suspects"] if s["lift"] == 1.0]
    assert "tests/test_admin.py::test_switch_store_to_euro" in [s["test"] for s in perfect], summary
    assert all(s.get("tied") for s in perfect) == (len(perfect) > 1), summary

    seed = data["shuffle"]["failing_seeds"][0]
    code, stdout, stderr = cli("polluter", target, "--seed", str(seed), "--scope", "tests", "--python", sys.executable)
    assert code == 0, stderr
    assert "Polluter: tests/test_admin.py::test_switch_store_to_euro" in stdout, stdout + stderr


def test_stable_test_is_not_reproduced(tmp_path):
    data = run_json(tmp_path, "tests/test_cart.py::test_cart_total", "--runs", "5", "--shuffle-runs", "3")
    assert data["verdict"] == "NOT-REPRODUCED"


def test_unknown_test_is_a_clear_error():
    code, stdout, stderr = cli("run", "tests/test_cart.py::test_does_not_exist", "--runs", "1", "--mode", "isolated",
                               "--python", sys.executable)
    assert code != 0
    assert "did not run" in (stdout + stderr)


def test_hints_point_at_the_causes():
    code, stdout, _ = cli("hints", "tests/test_reports.py::test_report_timestamp", "tests/test_admin.py")
    assert code == 0
    assert "[clock] generated_at = datetime.now()" in stdout          # followed into shop/reports.py
    assert "sets settings.currency on an imported object" in stdout


@pytest.mark.parametrize("args", [["--help"], ["--version"], ["scan", "--help"], ["run", "--help"]])
def test_cli_help(args):
    code, stdout, _ = cli(*args)
    assert code == 0 and stdout


def test_tied_suspects_are_flagged():
    from flake_doctor.rerun import suspects
    target = "t::victim"
    rows = []
    # polluter P and bystander B always sit on the same side of the victim; C varies independently
    for i, (before_pb, c_before) in enumerate([(1, 0), (1, 1), (0, 0), (0, 1), (1, 0), (0, 1)]):
        before = (["t::P", "t::B"] if before_pb else []) + (["t::C"] if c_before else [])
        order = before + [target] + [x for x in ("t::P", "t::B", "t::C") if x not in before]
        rows.append({"order": order, "before": before, "outcome": "failed" if before_pb else "passed"})
    found = suspects(rows, target)
    assert {s["test"] for s in found} == {"t::P", "t::B"}
    assert all(s.get("tied") for s in found)
