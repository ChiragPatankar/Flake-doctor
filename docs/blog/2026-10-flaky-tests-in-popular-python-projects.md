# I scanned a month of CI from pydantic, aiohttp, fastapi and pandas for flaky tests. Here's what I found.

*October 2026 · Chirag Patankar*

Every project with CI has a test that fails for no reason. Someone clicks **Re-run**, it goes green, and
everyone moves on. Nobody writes it down, so the same test wastes someone else's afternoon next week.

I wanted to know how much of this is going on in well-run open-source projects, so I built a tool to measure
it, [flake-doctor](https://github.com/ChiragPatankar/Flake-doctor), and pointed it at 30 days of GitHub
Actions history from four popular Python projects.

## The trick: same commit, different result

You can't tell a flaky test from a real bug by looking at one failure. But CI history contains a much
stronger signal: **a job that failed and then passed on the same commit**. Same code, different outcome.
Whatever failed in between is flaky by definition.

That happens all the time: someone re-runs a failed job, or the push run and the pull-request run of the
same commit disagree. flake-doctor lists a repo's workflow runs, fetches the jobs of failed runs (and of
passing runs on the same commits), downloads the failed jobs' logs, pulls the pytest failures out of them,
and checks which failures were followed by a pass of the same job on the same commit.

## What it found

| Project | Runs scanned | Failed jobs | Confirmed flaky tests |
|---------|-------------:|------------:|----------------------:|
| pydantic | 1,241 | 1,144 | 7 |
| aiohttp | 4,180 (25 days) | 610 | 7 |
| fastapi | 3,119 | 257 | 0 |
| pandas | 2,836 (7 days) | 500 | 0 |

(aiohttp and pandas run so much CI that the scan hit its 5,000-run cap before reaching 30 days.)

### aiohttp: the network is a liar

All seven of aiohttp's flaky tests are the kind you'd guess for an async HTTP library:

- keep-alive timeout tests failing with `ClientOSError: [Errno 104] Connection reset by peer`
- a WebSocket heartbeat test and a stalled-parser test failing with an unraisable exception raised in the
  finalizer of a `WebSocketWriter.send_frame` coroutine
- a read-timeout test hitting `SocketTimeoutError`
- a timing assertion: `assert 0.014999999999986358 == 0 ± 0.001`

Each one failed and then passed on the same commit. The timing one is my favourite: it expects a value of
zero, give or take a millisecond, and on that run it came out as 15 ms. Timing assertions and shared CI
runners don't mix.

### pydantic: the test that only fails after midnight

pydantic's best one is a classic. In `tests/test_datetime.py`:

```python
@pytest.mark.parametrize(
    'value',
    (
        date.today(),
        date.today() + timedelta(1),
        '2064-06-01',
    ),
)
def test_past_date_validation_fails(value, past_date_type):
    ...
    with pytest.raises(ValidationError):
        ...
```

`date.today()` inside `parametrize` runs when pytest **collects** the tests, not when it runs them. A
macOS job collected the tests on 10 October and ran them after midnight UTC. By then "today" was
yesterday, so `PastDate` happily accepted it:

```
value = datetime.date(2026, 10, 10), past_date_type = <class 'pydantic.types.PastDate'>
E       Failed: DID NOT RAISE ValidationError
```

The re-run, 50 minutes later, passed. This kind of failure never gets investigated: it happens rarely, to
someone else's job, late at night, and re-running fixes it.

### fastapi: the false positive I almost shipped

My first version also had a softer category, "suspect", for tests that fail on several commits across several
branches. On fastapi it flagged 29 tests that failed on 20 commits across 12 branches, all with
`DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated`.

That isn't flakiness. A new anyio release broke every branch at once until fastapi adapted. To tell the two
apart, I measured how often the *workflow* passed around each test's failures:

| | workflow pass rate around the failures |
|---|---|
| aiohttp's confirmed flaky tests | 68% to 86% |
| fastapi's "suspects" | 13% |

A flaky test fails *occasionally while CI is otherwise green*. A breakage turns *everything* red. So now a
test is only a suspect if its workflow kept passing most of its other runs. Otherwise it's treated as a
breakage and hidden.

### pandas: most red CI isn't tests at all

pandas had 500 failed jobs in a week and no flaky tests. The jobs that passed on retry had failed for
infrastructure reasons: an HTTP 500 while downloading a build tool, a pip build failure, a collection error.

That was the pattern everywhere. **Most failed CI jobs weren't test failures at all**: lint, type checks, docs
builds, preview-upload bots and infrastructure. Any tool that just counts red builds will mostly be counting
those.

## Things that surprised me while building it

- **GitHub's API silently caps filtered run listings at 1,000 results.** My first "30-day" scans covered 2 to
  10 days. flake-doctor now splits the time window until each piece fits under the cap.
- **pydantic uses pytest-pretty**, which replaces pytest's `FAILED` summary lines with a table. My parser found
  0 test names in 173 failed test jobs until I taught it to read the table, and to fall back on pytest's own
  failure sections.
- **The tool found a flaky test in its own test suite** the first time its GitHub Action ran. Two tests can tie
  perfectly as "the one that breaks the victim", because their positions in a shuffled order are correlated
  through the victim's position, and my code was picking one of them arbitrarily. Now it reports the tie and
  hands over to a bisection step that settles it.

## Try it

```bash
pipx install flake-doctor
export GITHUB_TOKEN=...     # read access to the repo's Actions
flake-doctor scan owner/repo
```

There's also a [GitHub Action](https://github.com/marketplace/actions/flake-doctor-flaky-test-report) that
keeps a "Flaky test report" issue up to date, local tools that rerun a test alone and in shuffled order to find
*why* it's flaky, and a Claude Code plugin. Everything is open source:
[github.com/ChiragPatankar/Flake-doctor](https://github.com/ChiragPatankar/Flake-doctor).

If you run it on your project, I'd love to hear what it finds, and especially where it gets things wrong.
