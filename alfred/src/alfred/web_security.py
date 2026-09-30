"""HTTP hardening: security headers, no-store on token pages, and a small rate limiter.

* Headers are added by one middleware so no route can forget them.
* Dashboard URLs carry a secret token: they must never be cached (browser, proxy) and the
  page must not leak the URL through ``Referer``.
* CORS is intentionally NOT enabled: the dashboard calls its own origin only, so any
  cross-origin browser request is refused by default. ``test_web_security`` pins that.
* The limiter is in-process (single web replica). It makes token guessing and scraping
  expensive; it is not a DDoS shield (that is the edge's job).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from alfred.settings import settings

# Chart.js is the only third-party script; SRI pins its exact bytes (see dashboard.py).
_DASHBOARD_CSP = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_DEFAULT_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
_TOKEN_PREFIXES = ("/d/", "/api/d/")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        path = request.url.path
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        is_html = response.headers.get("content-type", "").startswith("text/html")
        if path.startswith("/d/"):
            h.setdefault("Content-Security-Policy", _DASHBOARD_CSP)
        elif is_html and not path.startswith(("/docs", "/redoc")):
            h.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
            )
        elif not path.startswith(("/docs", "/redoc")):
            h.setdefault("Content-Security-Policy", _DEFAULT_CSP)
        if path.startswith(_TOKEN_PREFIXES):
            h["Cache-Control"] = "no-store"
            h["Pragma"] = "no-cache"
        if settings.environment == "production":
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class RateLimiter:
    """Sliding-window limiter: at most ``limit`` hits per ``window`` seconds per key."""

    def __init__(self, limit: int, window: float = 60.0) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self._hits[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self._hits) > 10_000:  # bound memory: drop idle keys
            for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                del self._hits[k]
        return True


def client_ip(request: Request) -> str:
    """The caller's IP. Behind Railway's proxy the LAST X-Forwarded-For entry is the one the
    proxy itself appended (earlier ones are client-controlled and spoofable)."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


_dashboard_limiter = RateLimiter(limit=settings.dashboard_rate_limit_per_minute)


async def limit_dashboard(request: Request) -> None:
    """FastAPI dependency for the token routes: 429 when one IP hammers them."""
    if not _dashboard_limiter.allow(client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers={"Retry-After": "60"},
        )
