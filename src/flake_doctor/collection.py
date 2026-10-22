"""Find non-deterministic values that are computed when tests are *collected*, not when they run.

pytest imports every test module before running anything. Whatever Python evaluates at that point
(decorator arguments such as ``@pytest.mark.parametrize(...)``, ``@pytest.fixture(params=...)``,
function default arguments, module-level and class-level assignments) is computed once, at import.
When that value comes from the clock, a random generator, or the process, the test later runs
against a value that may already be stale or that differs from one process to the next:

    @pytest.mark.parametrize("value", [date.today()])   # "today" as of collection
    def test_past_date_fails(value): ...                # a run that crosses midnight sees yesterday

That exact pattern made pydantic's PastDate/FutureDate tests fail on CI after midnight UTC
(pydantic#14030). This check finds it statically, before it costs anyone a rerun.

The idea came from a reader's comment on the flake-doctor launch post: an AST pass can see the one
direction CI history can't, namely the flaky tests that haven't failed yet.
"""

import ast

# Calls whose result depends on when, or in which process, they run.
CLOCK = {
    "datetime.date.today", "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.datetime.today",
    "time.time", "time.time_ns", "time.monotonic", "time.monotonic_ns", "time.perf_counter",
    "time.perf_counter_ns", "time.localtime", "time.gmtime", "time.strftime", "time.ctime", "time.asctime",
}
PROCESS = {
    "os.getpid", "os.getppid", "os.getenv", "os.urandom", "os.environ.get", "socket.gethostname",
    "tempfile.mkdtemp", "tempfile.mkstemp", "tempfile.gettempdir",
}
RANDOM_CALLS = {"uuid.uuid1", "uuid.uuid4"}
RANDOM_PREFIXES = ("random.", "secrets.", "numpy.random.", "faker.")
# Constructing a seeded generator is deterministic, so it's fine at collection time.
SEEDED = {"random.Random", "random.seed", "numpy.random.default_rng", "numpy.random.seed", "numpy.random.RandomState"}

WHY = {
    "clock": ("reads the clock when tests are collected, not when this test runs: a run that crosses midnight, "
              "or a slow collection, sees a stale value. Compute it inside the test (e.g. parametrize an offset "
              "and add it to the current time in the test body)."),
    "random": ("draws a random value when tests are collected: every pytest process (and each xdist worker) "
               "gets a different one, so a failure can't be reproduced by re-running the test. Seed it, or "
               "generate the value inside the test."),
    "process": ("reads the process or environment when tests are collected: it can differ between xdist "
                "workers and machines, and is fixed before fixtures like monkeypatch can change it. Read it "
                "inside the test."),
}
IMPORT_WHY = {
    "clock": "reads the clock when this module is imported (during test collection): tests see the import-time value, not the time they run",
    "random": "draws a random value when this module is imported (during test collection): it differs in every process",
    "process": "reads the process or environment when this module is imported (during test collection)",
}


def import_aliases(tree):
    """Map local names to the dotted names they stand for, e.g. {'date': 'datetime.date', 'np': 'numpy'}."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    aliases.setdefault(top, top)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                aliases[a.asname or a.name] = "%s.%s" % (node.module, a.name)
    return aliases


def dotted(node, aliases):
    """'datetime.datetime.now' for `dt.datetime.now` (with `import datetime as dt`), else None."""
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = dotted(node.value, aliases)
        return base and "%s.%s" % (base, node.attr)
    return None


def classify(name, call=None):
    """'clock' / 'random' / 'process' for a non-deterministic call or attribute, else None."""
    if name in SEEDED:
        return None
    if name in CLOCK:
        return "clock"
    if name in RANDOM_CALLS or name.startswith(RANDOM_PREFIXES):
        # random.Random(42) / numpy.random.default_rng(42) are deterministic; their methods aren't
        # resolvable statically, so only flag module-level random functions.
        return "random"
    if name in PROCESS or name == "os.environ" or name.startswith("os.environ."):
        return "process"
    return None


def eager_nodes(node):
    """Walk an expression, skipping code that only runs later (lambdas, nested defs)."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            stack.append(child)


ENV = {"os.environ", "os.getenv", "os.environ.get"}


def nondeterministic(expr, aliases, include_env=True):
    """[(lineno, 'clock'|'random'|'process', 'date.today()')] for values computed right away in expr.

    include_env=False skips environment reads, which at module level or in skip conditions are usually
    deliberate configuration (`skipif(os.environ.get("CI"))`) rather than test data.
    """
    found = {}
    for node in eager_nodes(expr):
        if isinstance(node, ast.Call):
            name = dotted(node.func, aliases)
            kind = name and classify(name)
            if kind:
                found[(node.lineno, name)] = (node.lineno, kind, ast.unparse(node.func) + "()")
        elif isinstance(node, (ast.Attribute, ast.Subscript)):
            target = node.value if isinstance(node, ast.Subscript) else node
            name = dotted(target, aliases)
            if name == "os.environ":
                found.setdefault((node.lineno, name), (node.lineno, "process", ast.unparse(target)))
    # `os.environ.get(...)` would otherwise also report the bare `os.environ` on the same line
    keep = {}
    for (line, name), item in found.items():
        if name == "os.environ" and any(l == line and n.startswith("os.environ.") for l, n in found):
            continue
        if not include_env and (name in ENV or name.startswith("os.environ.")):
            continue
        keep[(line, name)] = item
    return sorted(keep.values())


def _is_data_decorator(dec, aliases):
    """parametrize(...) and fixture(params=...) turn their arguments into test inputs."""
    if not isinstance(dec, ast.Call):
        return False
    name = dotted(dec.func, aliases) or ""
    return name.endswith("parametrize") or (name.endswith("fixture") and any(k.arg == "params" for k in dec.keywords))


def _definition_time_exprs(node, aliases):
    """(expr, is_test_data) evaluated when a def/class statement runs: decorators and default arguments."""
    exprs = [(d, _is_data_decorator(d, aliases)) for d in node.decorator_list]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        exprs += [(d, True) for d in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]]
    return exprs


def _import_time_exprs(body, aliases):
    """(expr, where, is_test_data) for everything in a module or class body that runs at import time."""
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from ((e, "definition", data) for e, data in _definition_time_exprs(stmt, aliases))
        elif isinstance(stmt, ast.ClassDef):
            yield from ((e, "definition", data) for e, data in _definition_time_exprs(stmt, aliases))
            yield from _import_time_exprs(stmt.body, aliases)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        elif isinstance(stmt, ast.If) and _is_main_guard(stmt):
            continue
        else:
            yield stmt, "statement", False


def _is_main_guard(stmt):
    test = stmt.test
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__")


def collection_time_hints(tree, is_test_file):
    """[(lineno, category, why)] for non-deterministic values computed at collection or import time.

    In test files (and conftest.py) everything that runs at import counts as collection time. In other
    project modules only module- and class-level statements are reported ("import-time"), because a
    library reading the clock in a function default is unusual and rarely what a test depends on.
    """
    aliases = import_aliases(tree)
    hints = []
    for expr, where, is_data in _import_time_exprs(tree.body, aliases):
        if not is_test_file and where != "statement":
            continue
        for lineno, kind, call in nondeterministic(expr, aliases, include_env=is_test_file and is_data):
            if is_test_file:
                hints.append((lineno, "collection-time", "%s %s" % (call, WHY[kind])))
            else:
                hints.append((lineno, "import-time", "%s %s" % (call, IMPORT_WHY[kind])))
    return hints


def is_test_path(path):
    name = path.name
    return name == "conftest.py" or (name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")))
