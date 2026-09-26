#!/usr/bin/env python3
"""M5 — Procrastinate worker for Alfred background jobs.

Railway start command for worker service:
    python /app/scripts/worker.py

Environment variables: same as the main app (.env / Railway env vars).
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import structlog

logger = structlog.get_logger()


async def main() -> None:
    from alfred.settings import settings
    logger.info("worker.start")
    # The worker imports alfred.jobs tasks and runs them from the queue.
    # For M5 the daily_cron.py sends directly, so this worker is a
    # future-proofing hook for high-volume / async retry use cases.
    # Placeholder — extend with procrastinate.App.run_worker_async() when needed.
    logger.info("worker.ready", note="Extend with procrastinate when job volume grows")
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
