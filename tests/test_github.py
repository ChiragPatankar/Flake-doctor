import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from flake_doctor.github import GitHub, GitHubError, find_token


class Recorder:
    def __init__(self):
        self.seen = []


def serve(handler_factory):
    server = HTTPServer(("127.0.0.1", 0), handler_factory)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


@pytest.fixture
def servers():
    storage_log = Recorder()

    class Storage(BaseHTTPRequestHandler):
        def do_GET(self):
            storage_log.seen.append(self.headers.get("Authorization"))
            body = b"FAILED tests/a.py::t - boom\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    storage, storage_url = serve(Storage)
    api_log = Recorder()

    class Api(BaseHTTPRequestHandler):
        def do_GET(self):
            api_log.seen.append((self.path, self.headers.get("Authorization")))
            if self.path.endswith("/logs"):
                self.send_response(302)
                self.send_header("Location", storage_url + "/blob?sig=abc")
                self.end_headers()
                return
            if self.path.startswith("/limited"):
                self.send_response(403)
                self.send_header("x-ratelimit-remaining", "0")
                self.send_header("x-ratelimit-reset", "9999999999")
                self.end_headers()
                return
            query = parse_qs(urlparse(self.path).query)
            page = int(query.get("page", ["1"])[0])
            items = [{"n": i} for i in range(100)] if page == 1 else [{"n": 100}]
            body = json.dumps({"things": items}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    api, api_url = serve(Api)
    yield api_url, api_log, storage_log
    api.shutdown()
    storage.shutdown()


def test_log_redirect_does_not_leak_token(servers, tmp_path):
    api_url, api_log, storage_log = servers
    gh = GitHub(token="secret-token", cache_dir=tmp_path, api=api_url)
    assert gh.get_text("/repos/o/r/actions/jobs/1/logs", cache=True).startswith("FAILED tests/a.py::t")
    assert api_log.seen[0][1] == "Bearer secret-token"
    assert storage_log.seen == [None]          # the token was not sent to the storage host
    # cached: a second read makes no requests
    before = gh.requests
    gh.get_text("/repos/o/r/actions/jobs/1/logs", cache=True)
    assert gh.requests == before


def test_paginate(servers):
    api_url, _, _ = servers
    gh = GitHub(api=api_url)
    assert len(gh.paginate("/things", "things")) == 101
    assert len(gh.paginate("/things", "things", limit=50)) == 50


def test_long_rate_limit_is_a_clear_error(servers):
    api_url, _, _ = servers
    with pytest.raises(GitHubError, match="rate limit"):
        GitHub(api=api_url).get_json("/limited")


def test_find_token_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", " abc \n")
    assert find_token() == "abc"
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("PATH", "")                     # no `gh`
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "flake-doctor").mkdir()
    (tmp_path / "flake-doctor" / "token").write_text("from-file\n")
    assert find_token() == "from-file"


def test_decode_log_handles_cp1252_lines_in_utf8_logs():
    from flake_doctor.github import decode_log
    raw = "│ table ┃\n".encode("utf-8") + b"assert 0.015 == 0 \xb1 0.001\n"
    assert decode_log(raw) == "│ table ┃\nassert 0.015 == 0 ± 0.001\n"
