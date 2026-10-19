# Flaky test causes and fixes (pytest)

Read the section for the cause you suspect. Each one says how to recognise it, how
to confirm it, how to fix it properly, and which tempting "fixes" only hide it.

Contents
1. Timing and concurrency
2. Real clock and timezones
3. Randomness
4. Hash and filesystem ordering
5. Shared state between tests (order dependence)
6. Network and external services
7. Files, ports and other shared resources
8. When the flake is a real bug
9. Fixes that hide the problem

---

## 1. Timing and concurrency

**Looks like:** `assert job.done` / `assert called` / missing row right after starting
background work; fails more under `--stress`; failing runs are often slower.

**Confirm:** `flake-doctor run --stress` raises the failure rate. The test (or the code it
calls) has `sleep()`, threads, executors, `asyncio.create_task`, callbacks, or a short timeout.

**Fix, best first:**
- Wait for the thing itself, not for time: `thread.join()`, `future.result()`,
  `await task`, `event.wait(timeout=5)`, or a queue `get(timeout=5)`. If the code under
  test hides the thread, expose a handle (return the future/thread, add `wait()` to the job).
- If there is genuinely nothing to join, poll for the condition with a generous deadline,
  so the test is fast when things are fast and only slow when they are slow:
  ```python
  def wait_until(predicate, timeout=5.0, interval=0.01):
      deadline = time.monotonic() + timeout
      while time.monotonic() < deadline:
          if predicate():
              return
          time.sleep(interval)
      raise AssertionError("condition not met within %ss" % timeout)
  ```
- Make the work synchronous in tests (inject an executor, or call the worker function directly)
  when the test is about the result, not the concurrency.
- Real data races in product code (unsynchronised shared counters, check-then-act) are bugs:
  fix them with a lock or atomic operation, see section 8.

**Avoid:** bigger `sleep()`s, longer timeouts on assertions about speed. They lower the rate
and slow the suite; the race is still there and returns on a slower CI machine.

## 2. Real clock and timezones

**Looks like:** two timestamps that differ by one second/minute/day; failures near midnight,
month end, DST changes, or only on CI (which usually runs in UTC).

**Confirm:** `flake-doctor hints` shows `datetime.now()`, `time.time()`, `date.today()` in the test
or the code under test. The test reads the clock more than once, or compares with "now".

**Fix, best first:**
- Freeze or inject the clock. Pass `now` (or a clock function) into the code, or freeze it:
  `freezegun.freeze_time("2026-03-01 12:00:00")` / `time_machine.travel(...)`, or
  `monkeypatch.setattr(module, "datetime", FakeDatetime)` for the module under test.
  Check what the project already uses before adding a dependency.
- If the value only needs to be "about now", assert a range captured around the call:
  ```python
  before = datetime.now().replace(microsecond=0)
  report = build_report(1)
  after = datetime.now().replace(microsecond=0)
  assert before <= report.generated_at <= after
  ```
- Timezone bugs: use aware datetimes and set `TZ`/the zone explicitly in the test; test the
  boundary dates on purpose (Dec 31, Feb 29, DST transitions).

**Avoid:** comparing with a tolerance like `abs(a - b) < 2s` without thinking about why they
differ; truncating both sides to coarser units (it moves the boundary, it doesn't remove it).

## 3. Randomness

**Looks like:** a specific generated value breaks the test; failure messages vary between runs.

**Confirm:** `random.*`, `uuid4`, Faker, numpy random without a seed; rerun with a fixed seed
and the outcome becomes stable (always pass or always fail).

**Fix, best first:**
- Decide what the test is about. If the code must work for *every* random value, the flake
  is a real bug the test found (section 8): fix the code, and add a deterministic test for the
  failing case.
- If randomness is incidental, make it deterministic: inject a `random.Random(seed)`, seed
  Faker per test, or pass explicit values.
- For property-style coverage, use Hypothesis, which shrinks and replays failing examples
  instead of failing randomly.

**Avoid:** seeding globally just to make a failing case disappear. A fixed seed that happens
to pass hides the bug for every other seed.

## 4. Hash and filesystem ordering

**Looks like:** same elements, different order (`['b', 'a'] == ['a', 'b']`); stable within one
process but varies between processes (Python randomises `str` hashing per process).

**Confirm:** `set()`/`frozenset` turned into a list or indexed, `os.listdir`, `glob`,
`iterdir`, SQL without `ORDER BY`. Running with a fixed `PYTHONHASHSEED` makes it stable.

**Fix:**
- If order matters to the behaviour, fix the code: `list(dict.fromkeys(items))` keeps first-seen
  order while removing duplicates; `sorted(...)` for listings; `ORDER BY` in queries.
- If order doesn't matter, compare without order: `sorted(a) == sorted(b)`, `set(a) == set(b)`,
  or `collections.Counter` when duplicates matter.

**Avoid:** setting `PYTHONHASHSEED` in CI or `pytest.ini`; it hides the bug instead of fixing it.

## 5. Shared state between tests (order dependence)

**Looks like:** passes alone, fails in the full suite or in some orders (`ORDER-DEPENDENT`).
The reverse (`NEEDS-OTHER-TESTS`): fails alone, passes only after some other test ran.

**Confirm:** `flake-doctor polluter` names the test that leaks state; read what it changes.
Typical leaks: module globals and settings objects, class attributes, singletons and
`lru_cache`, `os.environ`, `os.chdir`, `sys.path`/`sys.modules`, database rows, files,
module/session-scoped fixtures that a test mutates, mocks started without `stop()`.

**Fix, best first:**
- Fix the polluter so it cleans up after itself: use `monkeypatch.setattr/setenv/chdir/setitem`
  (undone automatically), `with mock.patch(...)`, or a fixture with `yield` and teardown.
- Give the victim its own setup instead of relying on whatever state it finds (fixture that
  builds what it needs; function-scoped fixture instead of a shared one it mutates).
- For caches/singletons, add an autouse fixture that resets them (`func.cache_clear()`).
- For `NEEDS-OTHER-TESTS`, move the missing setup into a fixture the dependent test requests.

**Avoid:** forcing an order (`pytest-order`, renaming files so they sort differently), or
marking the test to run alone. The leak stays and bites the next test added.

## 6. Network and external services

**Looks like:** timeouts, connection resets, 5xx, rate limits, or data changing upstream.

**Fix:** unit tests should not touch the network. Mock at the HTTP layer with what the project
already uses (`responses`, `respx`, `requests-mock`, `pytest-httpserver`, VCR cassettes), or inject
a fake client. Keep real-network checks in a separate, clearly marked integration suite, and
there wait for readiness instead of sleeping.

**Avoid:** retries around the assertion; increasing timeouts.

## 7. Files, ports and other shared resources

**Looks like:** "address already in use", "file exists", database locked, leftovers from a
previous run, failures only under `pytest -n` or parallel CI jobs.

**Fix:** `tmp_path`/`tmp_path_factory` for files, port `0` (let the OS choose) for servers,
unique database names or transactions rolled back per test, and always clean up in teardown.

## 8. When the flake is a real bug

Sometimes the test is right and the code is wrong: a race in production code, a uniqueness
guarantee that random collisions break, a date calculation wrong on boundary days, a function
documented to keep order that doesn't. Then:

- Fix the code, not the test, and keep (or add) a deterministic test that pins the failing case
  (e.g. force the colliding random values, freeze the clock at 23:59:59.9, call in the bad order).
- Tell the user plainly that the flake exposed a product bug, what users would see, and that
  the fix changes behaviour.
- If changing product code is out of scope or risky, say so and propose it rather than
  quietly loosening the test.

## 9. Fixes that hide the problem

These make the symptom rarer without removing the cause. Don't use them as the fix; if one
is truly the only option (e.g. a third-party service), say so explicitly and explain why.

- Retries: `@pytest.mark.flaky`, `pytest-rerunfailures`, `@retry`, loops around assertions
- Longer `sleep()`s or timeouts
- `@pytest.mark.skip`/`xfail`, deleting the test, or deleting/weakening the assertion
- Forcing test order, or pinning `PYTHONHASHSEED`/a random seed that happens to pass
- Catching and ignoring the exception
