from pathlib import Path
from typing import Any, cast

import pytest
from surrealdb import AsyncSurreal


def migration_sql(version: str) -> str:
    return Path(
        f"open_notebook/database/migrations/{version}.surrealql"
    ).read_text()


@pytest.mark.asyncio
async def test_migration_25_to_26_round_trip_preserves_v1_rows() -> None:
    database = AsyncSurreal("mem://")
    await database.use("course_v2_migration", "course_v2_migration")
    await database.query(
        "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
    )
    await database.query(migration_sql("24"))
    await database.query(migration_sql("25"))
    await database.query(
        """
        CREATE notebook:one SET name = 'V1 Notebook';
        CREATE course:one SET
            title = 'V1 Calculus', notebook = notebook:one,
            status = 'ready', language = 'zh-CN',
            source_ids = [], primary_source_ids = [], supplement_source_ids = [];
        CREATE course_version:one SET
            course = course:one, version_no = 1, status = 'published';
        CREATE chapter:one SET
            course_version = course_version:one, chapter_no = 1,
            chapter_key = 'limits', version_no = 1,
            title = 'Limits', status = 'published';
        CREATE progress:one SET
            course = course:one, chapter = chapter:one,
            chapter_key = 'limits', status = 'completed';
        """
    )
    before = await database.query(
        "SELECT title, status, language FROM course:one;"
        "SELECT chapter_key, status FROM progress:one;"
    )

    await database.query(migration_sql("26"))
    await database.query(
        """
        CREATE course_exercise:one SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'limits',
            exercise_key = 'limits-core-1', blueprint = {},
            difficulty = {}, grader = {};
        """
    )
    assert await database.query("SELECT exercise_key FROM course_exercise:one;") == [
        {"exercise_key": "limits-core-1"}
    ]

    await database.query(migration_sql("26_down"))
    after = await database.query(
        "SELECT title, status, language FROM course:one;"
        "SELECT chapter_key, status FROM progress:one;"
    )

    assert after == before
    database_info = cast(dict[str, Any], await database.query("INFO FOR DB;"))
    assert "course_exercise" not in database_info["tables"]
    await database.close()
