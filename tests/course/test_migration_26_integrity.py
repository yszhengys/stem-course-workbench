from pathlib import Path
from typing import Any, Protocol

import pytest
from surrealdb import AsyncSurreal


class QueryDatabase(Protocol):
    async def query(self, sql: str) -> Any: ...


def migration_sql(version: str) -> str:
    return Path(
        f"open_notebook/database/migrations/{version}.surrealql"
    ).read_text()


async def empty(database: QueryDatabase, table: str) -> bool:
    return await database.query(f"SELECT * FROM {table};") == []


@pytest.mark.asyncio
async def test_migration_26_version_and_tutor_cascades_are_real() -> None:
    database = AsyncSurreal("mem://")
    await database.use("course_v2_cascade", "course_v2_cascade")
    await database.query(
        "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
    )
    for version in ("24", "25", "26"):
        await database.query(migration_sql(version))
    await database.query(
        """
        CREATE notebook:one SET name = 'Notebook';
        CREATE course:one SET
            title = 'Calculus', notebook = notebook:one,
            source_ids = [], primary_source_ids = [], supplement_source_ids = [];
        CREATE course_version:one SET course = course:one, version_no = 1;
        CREATE chapter:one SET
            course_version = course_version:one, chapter_no = 1,
            chapter_key = 'limits', title = 'Limits';
        CREATE course_exercise:one SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'limits',
            exercise_key = 'core', blueprint = {}, difficulty = {}, grader = {};
        CREATE course_learning_event:one SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'limits', event_key = 'event-1',
            kind = 'chapter_opened', payload = {}, occurred_at = time::now();
        CREATE course_concept_mastery:one SET
            course = course:one, course_version = course_version:one,
            chapter_key = 'limits', concept_key = 'limit', snapshot_hash = 'hash';
        CREATE course_tutor_session:one SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'limits', model_selection = {};
        CREATE course_tutor_turn:one SET
            course = course:one, course_version = course_version:one,
            session = course_tutor_session:one, chapter_key = 'limits',
            turn_no = 1, role = 'assistant', content = 'Grounded';
        CREATE course_draft_revision:one SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'limits', revision_no = 1,
            base_artifact_hash = 'a', artifact_hash = 'b', operation = {};
        """
    )

    await database.query("DELETE course_tutor_session:one;")
    assert await empty(database, "course_tutor_turn")

    await database.query(
        """
        CREATE course_tutor_session:two SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'limits', model_selection = {};
        CREATE course_tutor_turn:two SET
            course = course:one, course_version = course_version:one,
            session = course_tutor_session:two, chapter_key = 'limits',
            turn_no = 1, role = 'assistant', content = 'Grounded';
        """
    )
    await database.query("DELETE course_version:one;")

    for table in (
        "course_exercise",
        "course_learning_event",
        "course_concept_mastery",
        "course_tutor_session",
        "course_tutor_turn",
        "course_draft_revision",
    ):
        assert await empty(database, table), table
    await database.close()


def test_migration_26_has_owned_query_indexes_and_chapter_scoped_mastery() -> None:
    up = migration_sql("26")
    down = migration_sql("26_down")

    assert "DEFINE EVENT OVERWRITE course_version_delete" in up
    assert "DEFINE EVENT OVERWRITE course_tutor_session_delete" in up
    assert "DEFINE EVENT OVERWRITE course_version_delete" in down
    assert "REMOVE EVENT IF EXISTS course_tutor_session_delete" in down
    assert (
        "FIELDS course, course_version, chapter_key, concept_key UNIQUE" in up
    )
    for table in (
        "course_exercise",
        "course_learning_event",
        "course_concept_mastery",
        "course_tutor_session",
        "course_tutor_turn",
        "course_draft_revision",
        "course_export",
    ):
        assert f"{table}_course_idx" in up
