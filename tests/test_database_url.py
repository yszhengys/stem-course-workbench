"""Tests for the SurrealDB connection URL resolution."""

import pytest

from open_notebook.database.repository import get_database_url


@pytest.fixture(autouse=True)
def _clean_db_env(monkeypatch):
    for var in ("SURREAL_URL", "SURREAL_ADDRESS", "SURREAL_PORT"):
        monkeypatch.delenv(var, raising=False)
    yield


def test_surreal_url_wins(monkeypatch):
    monkeypatch.setenv("SURREAL_URL", "ws://surrealdb:8000/rpc")
    assert get_database_url() == "ws://surrealdb:8000/rpc"


def test_legacy_fallback_builds_valid_ws_url():
    # The legacy fallback must produce a URL with the port in the authority
    # section — a port embedded in the path (e.g. ws://host/rpc:8000) never
    # reaches SurrealDB.
    assert get_database_url() == "ws://localhost:8000/rpc"


def test_legacy_fallback_respects_address_and_port(monkeypatch):
    monkeypatch.setenv("SURREAL_ADDRESS", "db.internal")
    monkeypatch.setenv("SURREAL_PORT", "8018")
    assert get_database_url() == "ws://db.internal:8018/rpc"
