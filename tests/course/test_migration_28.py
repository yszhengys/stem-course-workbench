from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from surrealdb import AsyncSurreal

from open_notebook.database.async_migrate import AsyncMigrationManager


class QueryDatabase(Protocol):
    async def query(self, sql: str) -> Any: ...


def migration_sql(version: str) -> str:
    return Path(
        f"open_notebook/database/migrations/{version}.surrealql"
    ).read_text(encoding="utf-8")


def test_migration_28_is_registered_and_only_adds_upgrade_lineage() -> None:
    manager = AsyncMigrationManager()
    up = migration_sql("28")
    down = migration_sql("28_down")

    assert len(manager.up_migrations) >= 28
    assert len(manager.down_migrations) >= 28
    assert manager.up_migrations[27].sql == " ".join(
        line.strip()
        for line in up.splitlines()
        if line.strip() and not line.strip().startswith("--")
    )
    for field in (
        "upgrade_source_version",
        "upgrade_idempotency_key",
        "upgrade_confirmation",
    ):
        assert f"DEFINE FIELD IF NOT EXISTS {field} ON TABLE course_version" in up
        assert f"REMOVE FIELD IF EXISTS {field} ON TABLE course_version" in down
    assert "course_version_upgrade_source_idx" in up
    assert "course_version_upgrade_source_idx" in down
    assert "REMOVE TABLE" not in down


@pytest.mark.asyncio
async def test_migration_28_round_trip_preserves_existing_versions() -> None:
    database = AsyncSurreal("mem://")
    await database.use("course_v2_migration_28", "course_v2_migration_28")
    try:
        await database.query(
            "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
        )
        for version in ("24", "25", "26", "27"):
            await database.query(migration_sql(version))
        await database.query(
            """
            CREATE notebook:one SET name = 'Notebook';
            CREATE course:one SET
                title = 'Calculus', notebook = notebook:one,
                status = 'ready', language = 'en', source_ids = [],
                primary_source_ids = [], supplement_source_ids = [];
            CREATE course_version:one SET
                course = course:one, version_no = 1, status = 'published';
            """
        )
        before = cast(
            list[dict[str, Any]],
            await database.query(
                "SELECT * OMIT created, updated FROM course_version:one;"
            ),
        )

        await database.query(migration_sql("28"))
        migrated = cast(
            list[dict[str, Any]],
            await database.query(
                "SELECT * OMIT created, updated FROM course_version:one;"
            ),
        )
        assert migrated == before
        await database.query(
            """
            CREATE course_version:two SET
                course = course:one, version_no = 2, status = 'generating',
                upgrade_source_version = course_version:one,
                upgrade_idempotency_key = 'upgrade-one',
                upgrade_confirmation = '创建学习升级版本';
            """
        )
        row = cast(
            dict[str, Any],
            await database.query("SELECT * FROM ONLY course_version:two;"),
        )
        assert str(row["upgrade_source_version"]) == "course_version:one"

        await database.query(migration_sql("28_down"))
        assert await database.query(
            "SELECT * OMIT created, updated FROM course_version:one;"
        ) == before
        downgraded = cast(
            dict[str, Any],
            await database.query("SELECT * FROM ONLY course_version:two;"),
        )
        assert "upgrade_source_version" not in downgraded
        assert "upgrade_idempotency_key" not in downgraded
        assert "upgrade_confirmation" not in downgraded
    finally:
        await database.close()
