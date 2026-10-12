"""Small JSON-over-HTTP helper with retries and polite rate limiting."""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

USER_AGENT = "kalshibot/0.1 (personal research)"
RETRIES = 4
MIN_INTERVAL_S = 0.25  # stay well under public rate limits

_last_call = 0.0


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} for {url}: {body[:300]}")
        self.status = status
        self.body = body


def _throttle() -> None:
    global _last_call
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def request_json(method: str, url: str, params: dict | None = None, body: dict | None = None,
                 headers: dict | None = None) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"

    for attempt in range(RETRIES):
        _throttle()
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            retryable = e.code == 429 or e.code >= 500
            # Never blindly retry a POST: an order may have gone through.
            if not retryable or method != "GET" or attempt == RETRIES - 1:
                raise HttpError(e.code, url, text) from e
            log.warning("HTTP %s on %s, retrying", e.code, url)
        except (urllib.error.URLError, TimeoutError) as e:
            if method != "GET" or attempt == RETRIES - 1:
                raise
            log.warning("network error on %s: %s, retrying", url, e)
        time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def get_json(url: str, params: dict | None = None) -> dict:
    return request_json("GET", url, params=params)
