"""Shared helpers: run pytest in a subprocess under flake-doctor's control."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# The pytest plugin is a standalone, stdlib-only file in this directory, so it can be put on the
# PYTHONPATH of the *project's* interpreter, which is usually not the one flake-doctor runs in.
PLUGIN_DIR = Path(__file__).resolve().parent


def find_python(project_root, explicit=None):
    """Prefer the project's virtualenv so the tests import the right dependencies."""
    if explicit:
        return explicit
    root = Path(project_root)
    for candidate in (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe", "venv/Scripts/python.exe"):
        path = root / candidate
        if path.exists():
            return str(path)
    venv = os.environ.get("VIRTUAL_ENV")
    if venv and (Path(venv) / "bin" / "python").exists():
        return str(Path(venv) / "bin" / "python")
    return sys.executable


def _importable(python, module):
    probe = subprocess.run(
        [python, "-c", "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec(%r) else 1)" % module],
        capture_output=True,
    )
    return probe.returncode == 0


class PytestRunner:
    """Runs pytest with plugins that would hide or distort flakiness switched off."""

    def __init__(self, project_root=".", python=None, extra_args=None):
        self.root = str(Path(project_root).resolve())
        self.python = find_python(self.root, python)
        self.extra_args = list(extra_args or [])
        check = subprocess.run([self.python, "-c", "import pytest"], capture_output=True, text=True)
        if check.returncode != 0:
            raise SystemExit(
                "flake-doctor: pytest is not importable with %s. Pass --python pointing at the "
                "project's interpreter (e.g. .venv/bin/python)." % self.python
            )
        self.base_args = ["-p", "_flake_doctor_plugin", "-p", "no:cacheprovider", "-p", "no:randomly", "-q"]
        # These plugins retry or parallelise tests, which hides the very failures we want to see.
        if _importable(self.python, "xdist"):
            self.base_args += ["-n", "0"]
        if _importable(self.python, "pytest_rerunfailures"):
            self.base_args += ["--reruns", "0"]
        if _importable(self.python, "flaky"):
            self.base_args += ["-p", "no:flaky"]
        # Coverage slows every run, and parallel runs (--workers) corrupt each other's data files,
        # which shows up as failures that have nothing to do with the test.
        if _importable(self.python, "pytest_cov"):
            self.base_args += ["--no-cov"]

    def run(self, args, targets=(), seed=None, order=None, timeout=600):
        """Run pytest once. Returns the plugin's report dict plus 'output' (tail of stdout)."""
        fd, report_path = tempfile.mkstemp(prefix="flake-doctor-", suffix=".json")
        os.close(fd)
        os.unlink(report_path)
        env = dict(os.environ)
        env.pop("PYTHONHASHSEED", None)  # let every run get its own hash seed, as on CI
        env.pop("PYTEST_ADDOPTS", None)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PLUGIN_DIR), env.get("PYTHONPATH")]))
        env["FLAKE_DOCTOR_REPORT"] = report_path
        env["FLAKE_DOCTOR_TARGETS"] = "\n".join(targets)
        env.pop("FLAKE_DOCTOR_SEED", None)
        env.pop("FLAKE_DOCTOR_ORDER", None)
        order_path = None
        if order is not None:
            fd, order_path = tempfile.mkstemp(prefix="flake-doctor-order-", suffix=".txt")
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(order))
            env["FLAKE_DOCTOR_ORDER"] = order_path
        elif seed is not None:
            env["FLAKE_DOCTOR_SEED"] = str(seed)
        cmd = [self.python, "-m", "pytest"] + self.base_args + self.extra_args + list(args)
        try:
            proc = subprocess.run(cmd, cwd=self.root, env=env, capture_output=True, text=True, timeout=timeout)
            output = (proc.stdout + proc.stderr)[-3000:]
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            message = "timed out after %ss" % timeout
            results = {t: {"outcome": "failed", "phase": "timeout", "message": message, "duration": timeout} for t in targets}
            return {"order": [], "results": results, "exitstatus": -1, "output": "TIMEOUT\n" + partial[-2000:]}
        finally:
            if order_path:
                os.unlink(order_path)
        try:
            with open(report_path) as f:
                report = json.load(f)
            os.unlink(report_path)
        except (OSError, ValueError):
            report = {"order": [], "results": {}, "exitstatus": proc.returncode}
        report["output"] = output
        return report


def files_for(nodeids):
    """The test files pytest must be given so that these node ids get collected."""
    seen = []
    for nodeid in nodeids:
        path = nodeid.split("::", 1)[0]
        if path not in seen:
            seen.append(path)
    return seen


_ADDR = re.compile(r"0x[0-9a-fA-F]+")
_NUM = re.compile(r"\d+(\.\d+)?")


def signature(message):
    """Collapse a failure message to a stable key so failures with different numbers group together."""
    first = (message or "").strip().splitlines()[0] if message else "<no message>"
    first = _ADDR.sub("0x?", first)
    first = _NUM.sub("N", first)
    return first[:200]


class CpuStress:
    """Busy-loop processes that make the machine slow, like a crowded CI runner."""

    def __init__(self, workers=None):
        self.workers = workers or os.cpu_count() or 2
        self.procs = []

    def __enter__(self):
        for _ in range(self.workers):
            self.procs.append(subprocess.Popen([sys.executable, "-c", "while True: pass"]))
        return self

    def __exit__(self, *exc):
        for proc in self.procs:
            proc.kill()
        for proc in self.procs:
            proc.wait()
