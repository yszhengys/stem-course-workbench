from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from surrealdb import AsyncSurreal

import open_notebook.database.async_migrate as migrate_module
from open_notebook.database.async_migrate import AsyncMigration, AsyncMigrationRunner

ROOT = Path(__file__).parents[1]


class _RecordingConnection:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any] | None]] = []

    async def query(
        self, sql: str, variables: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.queries.append((sql, variables))
        return []


def test_legacy_migration_7_removes_optional_table_safely() -> None:
    migration = (
        ROOT / "open_notebook" / "database" / "migrations" / "7.surrealql"
    ).read_text(encoding="utf-8")

    assert "REMOVE TABLE IF EXISTS speaker_profile;" in migration


@pytest.mark.asyncio
async def test_up_migration_and_version_record_use_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RecordingConnection()

    @asynccontextmanager
    async def fake_connection() -> AsyncIterator[_RecordingConnection]:
        yield connection

    monkeypatch.setattr(migrate_module, "db_connection", fake_connection)

    await AsyncMigration("DEFINE TABLE atomic_probe SCHEMALESS;").run(
        current_version=0,
        target_version=1,
    )

    assert len(connection.queries) == 1
    sql, variables = connection.queries[0]
    assert variables is None
    assert sql.startswith("BEGIN TRANSACTION;")
    assert "DEFINE TABLE atomic_probe SCHEMALESS;" in sql
    assert (
        "CREATE ONLY type::thing('_sbl_migrations', 1) "
        "SET version = 1, applied_at = time::now();"
    ) in sql
    assert sql.rstrip().endswith("COMMIT TRANSACTION;")


@pytest.mark.asyncio
async def test_down_migration_deletes_exact_current_version_in_same_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _RecordingConnection()

    @asynccontextmanager
    async def fake_connection() -> AsyncIterator[_RecordingConnection]:
        yield connection

    monkeypatch.setattr(migrate_module, "db_connection", fake_connection)

    await AsyncMigration("REMOVE TABLE IF EXISTS atomic_probe;").run(
        current_version=4,
        target_version=3,
    )

    sql, _variables = connection.queries[0]
    assert "REMOVE TABLE IF EXISTS atomic_probe;" in sql
    assert "DELETE type::thing('_sbl_migrations', 4);" in sql
    assert "type::thing('_sbl_migrations', 3)" not in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("current,target", [(0, 0), (1, 3), (3, 1), (-1, 0)])
async def test_migration_rejects_non_adjacent_or_negative_versions(
    current: int,
    target: int,
) -> None:
    with pytest.raises(ValueError, match="adjacent non-negative"):
        await AsyncMigration("RETURN true;").run(
            current_version=current,
            target_version=target,
        )


@pytest.mark.asyncio
async def test_runner_passes_explicit_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []

    class _Migration:
        async def run(self, *, current_version: int, target_version: int) -> None:
            calls.append((current_version, target_version))

    versions = iter([1, 2])

    async def fake_latest_version() -> int:
        return next(versions)

    monkeypatch.setattr(migrate_module, "get_latest_version", fake_latest_version)
    runner = AsyncMigrationRunner(
        up_migrations=[_Migration(), _Migration(), _Migration()],  # type: ignore[list-item]
        down_migrations=[_Migration(), _Migration(), _Migration()],  # type: ignore[list-item]
    )

    await runner.run_one_up()
    await runner.run_one_down()

    assert calls == [(1, 2), (2, 1)]


@pytest.mark.asyncio
async def test_failed_embedded_migration_rolls_back_data_and_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = AsyncSurreal("mem://")
    await database.connect("mem://")
    await database.use("atomic_migration_test", "atomic_migration_test")

    @asynccontextmanager
    async def embedded_connection() -> AsyncIterator[Any]:
        yield database

    monkeypatch.setattr(migrate_module, "db_connection", embedded_connection)
    migration = AsyncMigration(
        "CREATE atomic_probe:should_rollback SET value = 1; "
        "THROW 'intentional atomic migration failure';"
    )

    try:
        with pytest.raises(Exception, match="failed transaction|intentional"):
            await migration.run(current_version=0, target_version=1)

        assert await database.query("SELECT * FROM atomic_probe;") == []
        assert await database.query("SELECT * FROM _sbl_migrations;") == []
    finally:
        await database.close()
