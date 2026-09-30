"""Error tracking (Sentry) with PII scrubbing, and named alerts for the events we care about.

Sentry is optional: with no ``SENTRY_DSN`` everything here is a no-op besides logging.
Nothing a user wrote is ever sent: no request bodies, no local variables, no user object.
"""

from __future__ import annotations

import structlog

from alfred.settings import settings

logger = structlog.get_logger(__name__)
_enabled = False


def _scrub(event: dict, hint: dict) -> dict | None:
    """Drop everything that could carry personal data before an event leaves the process."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "headers", "query_string", "env"):
            request.pop(key, None)
    event.pop("user", None)
    for crumb in (event.get("breadcrumbs") or {}).get("values", []) or []:
        crumb.pop("data", None)
    return event


def init_sentry(service: str = "web") -> bool:
    """Start Sentry when a DSN is configured. Safe to call more than once."""
    global _enabled
    if _enabled or not settings.sentry_dsn:
        return _enabled
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=_scrub,
        server_name=service,
    )
    sentry_sdk.set_tag("service", service)
    _enabled = True
    logger.info("observability.sentry_enabled", service=service)
    return True


def alert(name: str, **context: object) -> None:
    """Log a warning and, when Sentry is on, raise a grouped alert named ``name``.

    ``context`` must be ids/counters only — never message text.
    """
    logger.warning(name, **context)
    if not _enabled:
        return
    import sentry_sdk

    with sentry_sdk.new_scope() as scope:
        scope.fingerprint = [name]  # one issue per alert type, not per occurrence
        for k, v in context.items():
            scope.set_tag(k, str(v))
        sentry_sdk.capture_message(name, level="warning")
