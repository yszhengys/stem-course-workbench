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


async def exercise_snapshot(database: QueryDatabase) -> object:
    return await database.query(
        "SELECT * OMIT created, updated FROM course_exercise ORDER BY id;"
    )


def test_migration_27_is_registered_after_26_and_only_extends_exercises() -> None:
    manager = AsyncMigrationManager()
    up = migration_sql("27")
    down = migration_sql("27_down")

    assert len(manager.up_migrations) >= 27
    assert len(manager.down_migrations) >= 27
    assert manager.up_migrations[26].sql == " ".join(
        line.strip()
        for line in up.splitlines()
        if line.strip() and not line.strip().startswith("--")
    )
    for field in ("verification", "generation_run", "review_run_ids"):
        assert f"DEFINE FIELD IF NOT EXISTS {field} ON TABLE course_exercise" in up
        assert f"REMOVE FIELD IF EXISTS {field} ON TABLE course_exercise" in down
    assert "course_exercise_generation_run_idx" in up
    assert "course_exercise_generation_run_idx" in down
    assert "UPDATE course_exercise UNSET" in down
    assert "REMOVE TABLE" not in down


@pytest.mark.asyncio
async def test_migration_27_round_trip_preserves_migration_26_exercise() -> None:
    database = AsyncSurreal("mem://")
    await database.use("course_v2_migration_27", "course_v2_migration_27")
    try:
        await database.query(
            "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
        )
        for version in ("24", "25", "26"):
            await database.query(migration_sql(version))
        await database.query(
            """
            CREATE notebook:one SET name = 'Notebook';
            CREATE course:one SET
                title = 'Mechanics', notebook = notebook:one,
                subject = 'physics', status = 'ready', language = 'en',
                source_ids = [], primary_source_ids = [], supplement_source_ids = [];
            CREATE course_version:one SET
                course = course:one, version_no = 1, status = 'outline_approved';
            CREATE chapter:one SET
                course_version = course_version:one, chapter_no = 1,
                chapter_key = 'motion', version_no = 1, title = 'Motion',
                status = 'ready', review_status = 'passed', validation_status = 'passed';
            CREATE course_exercise:one SET
                course = course:one, course_version = course_version:one,
                chapter = chapter:one, chapter_key = 'motion',
                exercise_key = 'motion-core',
                blueprint = { key: 'motion-core', chapter_key: 'motion' },
                source_anchor_ids = ['anchor:motion'],
                difficulty = { concept_count: 1, reasoning_steps: 2 },
                grader = { kind: 'numeric', expected: '9.8' },
                is_core = true, is_gating = true, is_source_level = false;
            """
        )
        before = await exercise_snapshot(database)

        await database.query(migration_sql("27"))
        table_info = cast(
            dict[str, Any],
            await database.query("INFO FOR TABLE course_exercise;"),
        )
        assert "course_exercise_generation_run_idx" in table_info["indexes"]
        migrated = await exercise_snapshot(database)
        assert isinstance(migrated, list) and len(migrated) == 1
        assert migrated[0]["verification"] == {
            "anchor_ids": [],
            "level": "L1",
            "method": "self_consistency",
        }
        assert migrated[0].get("generation_run") is None
        assert migrated[0]["review_run_ids"] == []

        await database.query(migration_sql("27_down"))
        assert await exercise_snapshot(database) == before
    finally:
        await database.close()
