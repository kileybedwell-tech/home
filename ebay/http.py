"""Minimal JSON-over-HTTPS transport for the eBay REST APIs.

Uses only the standard library so the CLI runs without a virtualenv.  Proxy
settings are picked up from the usual ``HTTPS_PROXY``/``https_proxy``
environment variables by ``urllib``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

USER_AGENT = "ebay-connect/1.0 (+https://github.com/kileybedwell-tech/home)"

#: Statuses worth retrying: eBay throttling plus transient gateway failures.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class EbayError(RuntimeError):
    """An error response from eBay, with the parsed ``errors`` array intact."""

    def __init__(self, status: int, url: str, payload: Any, body: str) -> None:
        self.status = status
        self.url = url
        self.payload = payload
        self.body = body
        self.errors = payload.get("errors", []) if isinstance(payload, dict) else []
        super().__init__(self._describe())

    def _describe(self) -> str:
        if self.errors:
            parts = []
            for err in self.errors:
                if not isinstance(err, dict):
                    continue
                text = err.get("longMessage") or err.get("message") or ""
                error_id = err.get("errorId")
                params = err.get("parameters") or []
                detail = ", ".join(
                    f"{p.get('name')}={p.get('value')}"
                    for p in params
                    if isinstance(p, dict)
                )
                label = f"[{error_id}] {text}" if error_id else text
                parts.append(f"{label} ({detail})" if detail else label)
            if parts:
                return f"HTTP {self.status}: " + "; ".join(parts)
        snippet = self.body.strip()[:400]
        return f"HTTP {self.status} from {self.url}" + (f": {snippet}" if snippet else "")


def _parse(body: bytes) -> Any:
    if not body:
        return None
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    json_body: Any = None,
    form_body: Mapping[str, str] | None = None,
    timeout: float = 30.0,
    max_attempts: int = 4,
) -> Any:
    """Perform one JSON request, retrying throttles and transient failures.

    Returns the decoded response body (``None`` for 204).  Raises
    :class:`EbayError` for any non-2xx response that is not retried.
    """
    if json_body is not None and form_body is not None:
        raise ValueError("pass json_body or form_body, not both")

    sent: dict[str, str] = {"Accept": "application/json", "User-Agent": USER_AGENT}
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        sent["Content-Type"] = "application/json"
    elif form_body is not None:
        data = urllib.parse.urlencode(form_body).encode("utf-8")
        sent["Content-Type"] = "application/x-www-form-urlencoded"
    if headers:
        sent.update(headers)

    delay = 1.0
    last_error: EbayError | None = None
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(url, data=data, headers=sent, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _parse(resp.read())
        except urllib.error.HTTPError as exc:  # non-2xx
            raw = exc.read()
            error = EbayError(exc.code, url, _parse(raw), raw.decode("utf-8", "replace"))
            if exc.code not in RETRY_STATUSES or attempt == max_attempts:
                raise error from None
            last_error = error
            wait = _retry_after(exc.headers.get("Retry-After")) or delay
        except urllib.error.URLError as exc:  # DNS, TLS, connection reset
            if attempt == max_attempts:
                raise RuntimeError(f"could not reach {url}: {exc.reason}") from exc
            wait = delay
        time.sleep(wait)
        delay *= 2
    assert last_error is not None  # unreachable: loop either returns or raises
    raise last_error


def _retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header, ignoring HTTP-date form (eBay sends seconds)."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return max(0.0, min(seconds, 60.0))
