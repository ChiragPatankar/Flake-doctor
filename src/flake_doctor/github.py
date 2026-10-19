"""Minimal GitHub REST client (stdlib only) with an on-disk cache for immutable data."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"
USER_AGENT = "flake-doctor (+https://github.com/ChiragPatankar/flake-doctor)"


class GitHubError(RuntimeError):
    pass


def decode_log(raw):
    """UTF-8, but Windows runners sometimes emit cp1252 lines (e.g. pytest's "±"): decode those per line."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        lines = []
        for line in raw.split(b"\n"):
            try:
                lines.append(line.decode("utf-8"))
            except UnicodeDecodeError:
                lines.append(line.decode("cp1252", errors="replace"))
        return "\n".join(lines)


def find_token():
    """GITHUB_TOKEN / GH_TOKEN, then `gh auth token`, then ~/.config/flake-doctor/token."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip()
    if shutil.which("gh"):
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "flake-doctor" / "token"
    if path.is_file():
        return path.read_text().strip()
    return None


def default_cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "flake-doctor"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    def __init__(self, token=None, cache_dir=None, api=API, verbose=False):
        self.token = token
        self.api = api.rstrip("/")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.verbose = verbose
        self.requests = 0
        self._no_redirect = urllib.request.build_opener(_NoRedirect)

    # -- low level ---------------------------------------------------------------------------

    def _headers(self, auth=True):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT,
                   "X-GitHub-Api-Version": "2022-11-28"}
        if auth and self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def _cache_path(self, key):
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / digest[:2] / digest

    def _open(self, request, opener=None):
        """Open a request, waiting out short rate-limit resets."""
        for attempt in range(5):
            self.requests += 1
            try:
                return (opener or urllib.request.build_opener()).open(request, timeout=60)
            except urllib.error.HTTPError as err:
                if err.code in (301, 302, 303, 307, 308):
                    return err
                remaining = err.headers.get("x-ratelimit-remaining")
                if err.code in (403, 429) and (remaining == "0" or err.headers.get("retry-after")):
                    reset = int(err.headers.get("x-ratelimit-reset") or 0)
                    wait = int(err.headers.get("retry-after") or max(reset - time.time(), 1)) + 1
                    if wait > 900:
                        raise GitHubError(
                            "GitHub API rate limit reached; it resets in %d minutes.%s"
                            % (wait // 60, "" if self.token else " Set GITHUB_TOKEN to get 5,000 requests/hour."))
                    print("rate limited; waiting %ds" % wait, file=sys.stderr)
                    time.sleep(wait)
                    continue
                if err.code >= 500 and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                body = err.read().decode(errors="replace")[:300]
                raise GitHubError("GitHub API %s for %s: %s" % (err.code, request.full_url, body)) from None
            except urllib.error.URLError as err:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise GitHubError("network error for %s: %s" % (request.full_url, err.reason)) from None
        raise GitHubError("giving up on %s" % request.full_url)

    def _url(self, path, params=None):
        url = path if path.startswith("http") else self.api + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        return url

    # -- public ------------------------------------------------------------------------------

    def get_json(self, path, params=None, cache=False):
        url = self._url(path, params)
        cached = self._cache_path("json:" + url) if cache else None
        if cached and cached.exists():
            return json.loads(cached.read_text())
        with self._open(urllib.request.Request(url, headers=self._headers())) as resp:
            data = json.load(resp)
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(data))
        return data

    def get_text(self, path, cache=False):
        """Fetch text, following redirects without forwarding the token to other hosts (job logs)."""
        url = self._url(path)
        cached = self._cache_path("text:" + url) if cache else None
        if cached and cached.exists():
            return cached.read_text(errors="replace")
        resp = self._open(urllib.request.Request(url, headers=self._headers()), opener=self._no_redirect)
        status = getattr(resp, "code", None) or getattr(resp, "status", 200)
        if status in (301, 302, 303, 307, 308):
            location = resp.headers["Location"]
            same_host = urllib.parse.urlparse(location).netloc == urllib.parse.urlparse(self.api).netloc
            resp = self._open(urllib.request.Request(location, headers=self._headers(auth=same_host)))
        with resp:
            text = decode_log(resp.read())
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(text)
        return text

    def paginate(self, path, key, params=None, limit=None, cache=False):
        params = dict(params or {}, per_page=100)
        page = 1
        items = []
        while True:
            data = self.get_json(path, dict(params, page=page), cache=cache)
            batch = data[key] if key else data
            items.extend(batch)
            if len(batch) < 100 or (limit and len(items) >= limit):
                return items[:limit] if limit else items
            page += 1

    def request(self, method, path, payload):
        req = urllib.request.Request(self._url(path), method=method, data=json.dumps(payload).encode(),
                                     headers=dict(self._headers(), **{"Content-Type": "application/json"}))
        with self._open(req) as resp:
            return json.load(resp)
