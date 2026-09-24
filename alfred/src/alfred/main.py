"""Alfred FastAPI application entry point."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from alfred.settings import settings
from alfred.webhook import router as webhook_router

logger = structlog.get_logger(__name__)

_start_time = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    logger.info("alfred.startup", environment=settings.environment)
    yield
    logger.info("alfred.shutdown")


app = FastAPI(
    title="Alfred",
    description="Assistente pessoal WhatsApp — Dutch market",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.include_router(webhook_router)


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
