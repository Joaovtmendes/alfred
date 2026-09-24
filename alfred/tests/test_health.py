"""Tests for the /health and /ready endpoints."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from alfred.main import app


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def test_health_returns_200(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200


async def test_health_body(client: AsyncClient) -> None:
    data = (await client.get("/health")).json()
    assert data["status"] == "ok"
    assert "environment" in data
    assert "uptime_seconds" in data


async def test_ready_returns_200(client: AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
