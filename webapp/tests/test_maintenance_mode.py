"""The maintenance switch blocks pages and keeps /health answering.

It exists so the PR #67 rollback can stop traffic before dropping columns the
running code maps, without relying on undocumented behaviour from the hosting
platform while a service is suspended.

The middleware is driven directly rather than through starlette's TestClient,
which needs httpx: this suite's dependencies are about to be frozen into a
release manifest, and a test convenience is not worth widening them.
"""

import asyncio
import types

import pytest

from app.maintenance import MAINTENANCE_ENV, maintenance_middleware, maintenance_mode_enabled

SERVED = object()


def _request(path, accept=""):
    return types.SimpleNamespace(url=types.SimpleNamespace(path=path), headers={"accept": accept})


async def _served(_request):
    return SERVED


def _get(path, accept=""):
    return asyncio.run(maintenance_middleware(_request(path, accept), _served))


def test_pages_are_served_when_the_switch_is_off(monkeypatch):
    monkeypatch.delenv(MAINTENANCE_ENV, raising=False)
    assert _get("/players/x") is SERVED


def test_pages_are_blocked_when_the_switch_is_on(monkeypatch):
    monkeypatch.setenv(MAINTENANCE_ENV, "1")
    response = _get("/players/x")
    assert response.status_code == 503
    assert b"scoring update" in response.body
    assert response.headers["retry-after"] == "900"


def test_health_answers_while_blocked(monkeypatch):
    """The platform's health check must still pass, and an operator must be able
    to tell a deliberate block from an outage."""
    monkeypatch.setenv(MAINTENANCE_ENV, "1")
    assert _get("/health") is SERVED


def test_static_files_still_load_while_blocked(monkeypatch):
    monkeypatch.setenv(MAINTENANCE_ENV, "1")
    assert _get("/static/site.css") is SERVED


def test_a_json_client_gets_json(monkeypatch):
    monkeypatch.setenv(MAINTENANCE_ENV, "1")
    response = _get("/players/x", accept="application/json")
    assert response.status_code == 503
    assert b'"maintenance"' in response.body


@pytest.mark.parametrize("value,blocked", [("1", True), ("true", True), ("ON", True),
                                           ("0", False), ("", False), ("no", False)])
def test_only_affirmative_values_block(monkeypatch, value, blocked):
    monkeypatch.setenv(MAINTENANCE_ENV, value)
    assert maintenance_mode_enabled() is blocked
    assert (_get("/players/x") is not SERVED) is blocked
