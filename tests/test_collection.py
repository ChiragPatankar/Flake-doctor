import ast
import json
import textwrap

from flake_doctor.collection import collection_time_hints
from flake_doctor.hints import main as hints_main


def found(source, is_test_file=True):
    """{line: category} for collection/import-time findings in `source`."""
    tree = ast.parse(textwrap.dedent(source))
    return {line: cat for line, cat, _ in collection_time_hints(tree, is_test_file)}


def test_pydantic_midnight_pattern():
    # the shape of pydantic#14030: date.today() inside parametrize
    assert found("""
        from datetime import date, timedelta
        import pytest

        @pytest.mark.parametrize("value", (date.today(), date.today() + timedelta(1), "2064-06-01"))
        def test_past_date_validation_fails(value):
            assert value
    """) == {5: "collection-time"}


def test_aliases_are_resolved():
    assert found("""
        import datetime as dt
        import time as t
        from time import monotonic
        import numpy as np
        import pytest

        @pytest.mark.parametrize("a", [dt.datetime.now()])
        @pytest.mark.parametrize("b", [t.time(), monotonic()])
        @pytest.mark.parametrize("c", [np.random.rand()])
        def test_x(a, b, c): ...
    """) == {8: "collection-time", 9: "collection-time", 10: "collection-time"}


def test_lazy_and_seeded_values_are_fine():
    assert found("""
        import random
        from datetime import date
        import pytest

        @pytest.mark.parametrize("make", [lambda: date.today()])       # evaluated in the test
        @pytest.mark.parametrize("rng", [random.Random(42)])            # seeded
        def test_x(make, rng):
            assert make() <= date.today()                               # inside the body: fine
    """) == {}


def test_fixture_params_defaults_module_and_class_level():
    assert found("""
        import random, time, uuid
        import pytest

        STARTED = time.time()

        @pytest.fixture(params=[uuid.uuid4()])
        def ident(request):
            return request.param

        def test_default(seed=random.randint(0, 9)): ...

        class TestThing:
            created = time.time()

            def test_method(self, when=time.time()): ...
    """) == {5: "collection-time", 7: "collection-time", 11: "collection-time",
             14: "collection-time", 16: "collection-time"}


def test_environment_only_counts_as_test_data():
    assert found("""
        import os
        import pytest

        TOKEN = os.getenv("TOKEN")                                      # config: fine
        pytestmark = pytest.mark.skipif(not os.environ.get("CI"), reason="CI only")

        @pytest.mark.skipif("HOME" not in os.environ, reason="x")      # config: fine
        @pytest.mark.parametrize("user", [os.environ["USER"], os.getpid()])
        def test_x(user): ...
    """) == {9: "collection-time"}


def test_clock_in_skip_conditions_is_reported():
    # a skip decided at collection is decided by the collection-time clock
    assert found("""
        from datetime import datetime
        import pytest

        @pytest.mark.skipif(datetime.now().year > 2027, reason="expires")
        def test_x(): ...
    """) == {5: "collection-time"}


def test_main_guard_is_ignored():
    assert found("""
        import time
        if __name__ == "__main__":
            print(time.time())
    """) == {}


def test_project_modules_report_import_time_only():
    source = """
        import random, time

        BOOT_ID = random.random()

        def stamp(now=time.time()):          # unusual in library code; not reported there
            return now
    """
    assert found(source, is_test_file=False) == {4: "import-time"}
    assert found(source, is_test_file=True) == {4: "collection-time", 6: "collection-time"}


def test_folder_audit_only_and_fail(tmp_path, capsys):
    tests = tmp_path / "tests"
    (tests / "sub").mkdir(parents=True)
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tests / "test_clean.py").write_text("def test_ok():\n    assert 1\n")
    (tests / "sub" / "test_dates.py").write_text(
        "from datetime import date\nimport pytest\n\n"
        "@pytest.mark.parametrize('d', [date.today()])\ndef test_d(d):\n    import time\n    time.sleep(1)\n")
    (tmp_path / ".venv" / "lib" / "test_vendored.py").write_text(
        "import time\nimport pytest\n@pytest.mark.parametrize('t', [time.time()])\ndef test_t(t): ...\n")

    code = hints_main([".", "--root", str(tmp_path), "--only", "collection-time", "--fail", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert set(report) == {"tests/test_clean.py", "tests/sub/test_dates.py"}      # .venv skipped
    assert [h["line"] for h in report["tests/sub/test_dates.py"]] == [4]          # sleep() filtered out by --only

    (tests / "sub" / "test_dates.py").write_text("def test_d():\n    assert 1\n")
    assert hints_main([".", "--root", str(tmp_path), "--only", "collection-time", "--fail"]) == 0
