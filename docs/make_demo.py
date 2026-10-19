"""Generate docs/demo.svg: an animated terminal replaying a real `flake-doctor scan` (aiohttp, Oct 2026).

GitHub renders SVG animations inline in READMEs (no JavaScript needed). Run: python docs/make_demo.py
"""

from pathlib import Path
from xml.sax.saxutils import escape

COMMAND = "flake-doctor scan aio-libs/aiohttp"
# (text, colour, delay in seconds after the command finishes typing)
OUTPUT = [
    ("listing workflow runs since 2026-09-19... 4180 runs", "dim", 0.3),
    ("fetching jobs for 426 failed or re-run runs.....................", "dim", 0.8),
    ("fetching jobs for 41 passing runs on the same commits..", "dim", 1.3),
    ("reading 400 job logs....................", "dim", 1.8),
    ("", None, 2.3),
    ("aio-libs/aiohttp: 4180 runs, 2026-09-24 to 2026-10-19", "bold", 2.4),
    ("flaky: 7   suspect: 0   real failures (hidden): 15   failed jobs: 610", "bold", 2.6),
    ("", None, 2.8),
    ("[FLAKY] tests/test_client_functional.py::test_keepalive_timeout_async_sleep", "flaky", 3.1),
    ("    passed on the same commit after failing 3x; 3 failures, 21 CI min", "text", 3.3),
    ("    aiohttp.client_exceptions.ClientOSError: [Errno 104] Connection reset by peer", "err", 3.5),
    ("[FLAKY] tests/test_web_websocket.py::test_receive_does_not_reset_heartbeat", "flaky", 3.9),
    ("    passed on the same commit after failing 2x; 2 failures, 18 CI min", "text", 4.1),
    ("    pytest.PytestUnraisableExceptionWarning: Exception ignored in: finalizer ...", "err", 4.3),
    ("[FLAKY] tests/test_helpers.py::test_when_timeout_smaller_second[pyloop]", "flaky", 4.7),
    ("    passed on the same commit after failing 1x; 1 failures, 5 CI min", "text", 4.9),
    ("    assert 0.014999999999986358 == 0 ± 0.001", "err", 5.1),
    ("... 4 more", "dim", 5.4),
    ("", None, 5.5),
    ("jobs that failed and then passed on the same commit:", "text", 5.7),
    ("   29/258 Labels / Backport label added", "dim", 5.9),
    ("    6/13  CI / Test (pypy-3.11, Y, ubuntu, false)", "dim", 6.0),
]
COLOURS = {"dim": "#8b949e", "bold": "#f0f6fc", "flaky": "#f0883e", "text": "#c9d1d9", "err": "#ff7b72"}
CYCLE = 16.0          # seconds per loop
TYPE_TIME = 1.6       # seconds to type the command
HOLD_UNTIL = 15.0     # everything stays visible until here, then fades for the next loop

W, LINE, TOP, LEFT = 900, 19, 58, 20
height = TOP + LINE * (len(OUTPUT) + 1) + 24


def pct(t):
    return "%.2f%%" % (100.0 * t / CYCLE)


def appear(name, t):
    """Keyframes: hidden until t, visible until HOLD_UNTIL, hidden again by the end of the cycle."""
    return ("@keyframes %s { 0%%, %s { opacity: 0 } %s, %s { opacity: 1 } %s, 100%% { opacity: 0 } }"
            % (name, pct(max(t - 0.01, 0)), pct(t), pct(HOLD_UNTIL), pct(HOLD_UNTIL + 0.4)))


css = [
    "text { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; white-space: pre; }",
    "@keyframes type { 0%% { width: 0 } %s { width: 0 } %s, %s { width: %dpx } %s, 100%% { width: 0 } }"
    % (pct(0.3), pct(0.3 + TYPE_TIME), pct(HOLD_UNTIL), 8 * len(COMMAND) + 4, pct(HOLD_UNTIL + 0.4)),
    "#typing { animation: type %.0fs steps(%d, end) infinite; }" % (CYCLE, len(COMMAND)),
    "@keyframes blink { 0%, 49% { opacity: 1 } 50%, 100% { opacity: 0 } }",
    ".cursor { animation: blink 1s step-end infinite; }",
]
lines = []
y = TOP
lines.append('<text x="%d" y="%d" fill="#7ee787">$</text>' % (LEFT, y))
lines.append('<clipPath id="cmdclip"><rect id="typing" x="%d" y="%d" height="%d" width="0"/></clipPath>'
             % (LEFT + 16, y - 14, LINE))
lines.append('<text x="%d" y="%d" fill="#f0f6fc" clip-path="url(#cmdclip)">%s</text>' % (LEFT + 16, y, escape(COMMAND)))
for i, (text, colour, delay) in enumerate(OUTPUT):
    y += LINE
    if not text:
        continue
    name = "l%d" % i
    css.append(appear(name, 0.3 + TYPE_TIME + delay))
    css.append("#%s { opacity: 0; animation: %s %.0fs linear infinite; }" % (name, name, CYCLE))
    lines.append('<text id="%s" x="%d" y="%d" fill="%s">%s</text>' % (name, LEFT, y, COLOURS[colour], escape(text)))
y += LINE
css.append(appear("prompt", 0.3 + TYPE_TIME + OUTPUT[-1][2] + 0.3))
css.append("#prompt { opacity: 0; animation: prompt %.0fs linear infinite; }" % CYCLE)
lines.append('<g id="prompt"><text x="%d" y="%d" fill="#7ee787">$</text>'
             '<rect class="cursor" x="%d" y="%d" width="8" height="15" fill="#c9d1d9"/></g>' % (LEFT, y, LEFT + 16, y - 12))

svg = """<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img"
     aria-label="Animated terminal: flake-doctor scan aio-libs/aiohttp finds 7 flaky tests in 30 days of CI history">
<title>flake-doctor scan demo</title>
<style>
{css}
</style>
<rect width="{w}" height="{h}" rx="10" fill="#0d1117"/>
<rect width="{w}" height="32" rx="10" fill="#161b22"/>
<rect y="22" width="{w}" height="10" fill="#161b22"/>
<circle cx="20" cy="16" r="6" fill="#ff5f56"/><circle cx="40" cy="16" r="6" fill="#ffbd2e"/><circle cx="60" cy="16" r="6" fill="#27c93f"/>
<text x="{mid}" y="20" fill="#8b949e" text-anchor="middle" style="font-size:12px">flake-doctor scan</text>
{body}
</svg>
""".format(w=W, h=height, mid=W // 2, css="\n".join(css), body="\n".join(lines))
Path(__file__).with_name("demo.svg").write_text(svg)
print("wrote docs/demo.svg (%d bytes)" % len(svg))
