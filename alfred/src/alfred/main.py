"""Alfred FastAPI application entry point."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from alfred.dashboard import router as dashboard_router
from alfred.legal import router as legal_router
from alfred.settings import settings
from alfred.webhook import recovery_loop
from alfred.webhook import router as webhook_router

logger = structlog.get_logger(__name__)

_start_time = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    logger.info(
        "alfred.startup", environment=settings.environment, base_url=settings.base_url or "<EMPTY>"
    )
    # Safety net for the webhook's background processing (see alfred.webhook).
    recovery = asyncio.create_task(recovery_loop(), name="webhook-recovery")
    try:
        yield
    finally:
        recovery.cancel()
        logger.info("alfred.shutdown")


app = FastAPI(
    title="Alfred",
    description="Assistente pessoal WhatsApp — Dutch market",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    openapi_url="/openapi.json" if settings.environment != "production" else None,
    redoc_url=None,
)

app.include_router(webhook_router)
app.include_router(dashboard_router)
app.include_router(legal_router)


@app.get("/health", tags=["ops"])
async def health() -> JSONResponse:
    return JSONResponse(
        content={
            "status": "ok",
            "environment": settings.environment,
            "uptime_seconds": round(time.time() - _start_time, 1),
        }
    )


@app.get("/ready", tags=["ops"])
async def readiness() -> JSONResponse:
    return JSONResponse(content={"status": "ready"})
