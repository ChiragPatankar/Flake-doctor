"""Point at code patterns that commonly make tests flaky.

Scans a test file and, by default, the project modules it imports (one level
deep), and lists suspicious lines grouped by cause. These are hints for where to
look, not proof: confirm with `flake-doctor run` and by reading the code.

Usage:
  flake-doctor hints tests/test_orders.py::test_checkout
  flake-doctor hints tests/test_orders.py src/orders/service.py --no-follow
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# (category, regex, why)
PATTERNS = [
    ("timing", r"\btime\.sleep\(|\basyncio\.sleep\(", "fixed sleeps race against real work; wait for a condition or use a fake clock"),
    ("timing", r"\bthreading\.(Thread|Timer)\(|\bThreadPoolExecutor\(|\bProcessPoolExecutor\(|\bmultiprocessing\.", "background work finishes at an unpredictable time"),
    ("timing", r"\basyncio\.(create_task|ensure_future|gather|wait_for)\(|\bloop\.call_later\(", "async tasks may not have run yet when the assert happens"),
    ("timing", r"\btimeout\s*=\s*[\d.]+|\.join\(\s*[\d.]+|\.wait\(\s*[\d.]+", "short timeouts fail on slow or busy machines"),
    ("timing", r"assert .*(elapsed|duration|took|perf_counter|monotonic)", "asserting on how long something took depends on machine speed"),
    ("clock", r"\bdatetime\.(now|utcnow|today)\(|\bdate\.today\(|\btime\.time\(|\btime\.localtime\(|\btime\.strftime\(", "real clock: results change across second/day/month boundaries, DST and timezones"),
    ("clock", r"\btimezone\b|\btzinfo\b|\bTZ\b|\bpytz\b|\bzoneinfo\b", "timezone-dependent: CI often runs in UTC, laptops do not"),
    ("randomness", r"\brandom\.(random|randint|choice|choices|shuffle|sample|uniform|randrange|getrandbits)\(", "unseeded randomness: some values trigger the failure"),
    ("randomness", r"\buuid\.uuid[14]\(|\bsecrets\.|\bnp\.random\.|\bnumpy\.random\.|\bFaker\(|\bfaker\.", "random/generated data differs every run"),
    ("ordering", r"\blist\(\s*set\(|\bnext\(\s*iter\(\s*set\(|\bset\([^)]*\)\s*\)?\s*\[|\bfrozenset\(", "set order depends on hashing, which is randomised per process for strings"),
    ("ordering", r"\bos\.listdir\(|\bos\.scandir\(|\.iterdir\(|\bglob\.glob\(|\.r?glob\(|\bos\.walk\(", "filesystem listing order is not guaranteed; sort it"),
    ("ordering", r"\bhash\(|\bid\(", "hash()/id() values change between runs"),
    ("ordering", r"ORDER BY|\.all\(\)\[\s*0\s*\]|\.first\(\)", "database rows without ORDER BY come back in any order"),
    ("network", r"\brequests\.|\bhttpx\.|\burllib\.request|\baiohttp\.|\bhttp\.client|\bsmtplib\.|\bboto3\.|\bsocket\.(socket|create_connection)", "real network calls are slow and sometimes fail; mock them"),
    ("network", r"https?://(?!localhost|127\.0\.0\.1|example\.(com|org))", "talks to an external host"),
    ("network", r"\blocalhost:\d+|127\.0\.0\.1:\d+|\bport\s*=\s*\d{2,5}|\.bind\(\(", "fixed ports collide with other tests or processes; bind to port 0"),
    ("shared-state", r"^\s*global\s+\w+", "test or code mutates a module global that other tests see"),
    ("shared-state", r"\bos\.environ\[[^\]]+\]\s*=|\bos\.environ\.(update|setdefault|pop)\(|\bos\.putenv\(", "environment changes leak into later tests; use monkeypatch.setenv"),
    ("shared-state", r"\bos\.chdir\(", "changing directory leaks into later tests; use monkeypatch.chdir"),
    ("shared-state", r"scope\s*=\s*[\"'](module|session|package|class)[\"']", "a shared fixture: one test's changes are visible to the next"),
    ("shared-state", r"@(functools\.)?(lru_cache|cache)\b|\b_instance\b|\bsingleton\b", "caches and singletons keep state between tests"),
    ("shared-state", r"\bsys\.modules\[|\bimportlib\.reload\(", "module-level state changes are global"),
    ("filesystem", r"[\"']/tmp/|\btempfile\.mktemp\(|[\"'][\w./-]+\.(db|sqlite3?|json|csv|txt|log)[\"']", "fixed file paths are shared between tests and parallel runs; use tmp_path"),
]
COMPILED = [(c, re.compile(p), why) for c, p, why in PATTERNS]


def module_mutations(tree):
    """Assignments/mutations to attributes of imported names, and mutable module globals."""
    hints = []
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(a.asname or a.name for a in node.names)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(getattr(node, "value", None), (ast.List, ast.Dict, ast.Set)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            empty = not (getattr(node.value, "elts", None) or getattr(node.value, "keys", None))
            # Non-empty ALL_CAPS containers are usually constants; empty or lowercase ones are usually registries.
            if names and (empty or not all(n.isupper() for n in names)):
                hints.append((node.lineno, "shared-state", "module-level mutable %s: anything that appends/updates it leaks between tests" % ", ".join(names)))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            targets = []
            if isinstance(sub, ast.Assign):
                targets = sub.targets
            elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
                targets = [sub.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in imported:
                    hints.append((sub.lineno, "shared-state",
                                  "sets %s.%s on an imported object without restoring it; use monkeypatch.setattr"
                                  % (t.value.id, t.attr)))
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr in ("append", "extend", "update", "add", "insert", "setdefault", "clear", "pop") \
                    and isinstance(sub.func.value, ast.Attribute) and isinstance(sub.func.value.value, ast.Name) \
                    and sub.func.value.value.id in imported:
                hints.append((sub.lineno, "shared-state",
                              "mutates %s.%s (imported, shared by all tests)" % (sub.func.value.value.id, sub.func.value.attr)))
    return hints


def local_imports(tree, path, root):
    """Project files imported by `path` (absolute or relative imports that resolve under root)."""
    found = []
    bases = [root, root / "src", path.parent]
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            base = node.module
            names = [base] + ["%s.%s" % (base, a.name) for a in node.names]
        for name in names:
            rel = Path(*name.split("."))
            for base in bases:
                for candidate in (base / rel.with_suffix(".py"), base / rel / "__init__.py"):
                    if candidate.is_file() and candidate.resolve() != path.resolve() and candidate not in found:
                        found.append(candidate)
    return found


def function_span(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node.lineno, getattr(node, "end_lineno", node.lineno)
    return None


def scan_file(path, func=None):
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        tree = None
    span = function_span(tree, func) if (tree and func) else None
    hints = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for category, rx, why in COMPILED:
            if rx.search(line):
                hints.append((i, category, why))
                break
    if tree is not None:
        hints += module_mutations(tree)
    seen = set()
    out = []
    for lineno, category, why in sorted(hints):
        if (lineno, category) in seen:
            continue
        seen.add((lineno, category))
        out.append({
            "line": lineno,
            "category": category,
            "code": lines[lineno - 1].strip()[:120] if lineno - 1 < len(lines) else "",
            "why": why,
            "in_target": bool(span and span[0] <= lineno <= span[1]),
        })
    return out, tree


def main(argv=None):
    parser = argparse.ArgumentParser(prog="flake-doctor hints", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="test node id(s) or file paths")
    parser.add_argument("--root", default=".", help="project root (for resolving imports)")
    parser.add_argument("--no-follow", action="store_true", help="don't scan imported project modules")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    queue = []
    for p in args.paths:
        file_part, _, func = p.partition("::")
        func = func.split("::")[-1].split("[")[0] if func else None
        queue.append((Path(file_part) if Path(file_part).is_absolute() else root / file_part, func, True))

    report = {}
    done = set()
    while queue:
        path, func, given = queue.pop(0)
        if path in done or not path.is_file():
            if not path.is_file():
                print("skip (not found): %s" % path, file=sys.stderr)
            continue
        done.add(path)
        hints, tree = scan_file(path, func)
        try:
            key = str(path.relative_to(root))
        except ValueError:
            key = str(path)
        report[key] = hints
        if given and not args.no_follow and tree is not None:
            for dep in local_imports(tree, path, root):
                if dep not in done:
                    queue.append((dep, None, False))

    if args.json:
        print(json.dumps(report, indent=2))
        return
    total = 0
    clean = [k for k, h in report.items() if not h]
    for key, hints in report.items():
        if not hints:
            continue
        print("\n%s" % key)
        for h in hints:
            total += 1
            mark = "*" if h["in_target"] else " "
            print(" %s L%-4d [%s] %s\n          -> %s" % (mark, h["line"], h["category"], h["code"], h["why"]))
    if clean:
        print("\nNo hints in: %s" % ", ".join(clean))
    print("\n%d hint(s). '*' = inside the target test. Hints are leads, not proof." % total)

