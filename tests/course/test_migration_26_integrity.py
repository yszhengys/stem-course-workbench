from pathlib import Path
from typing import Any, Protocol, cast

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
            chapter_key = 'limits', concept_key = 'limit', snapshot_hash = 'hash',
            pending_transfers = [{
                chapter_key: 'limits', concept_key: 'limit',
                exercise_key: 'core', source_attempt_key: 'attempt-one',
                transfer_task_key: 'core-transfer'
            }];
        CREATE course_tutor_session:one SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'limits', model_selection = {};
        CREATE course_tutor_operation:one SET
            course = course:one, course_version = course_version:one,
            session = course_tutor_session:one, chapter_key = 'limits',
            operation_identity = 'tutor-message-one',
            operation_key = 'tutor-message-one-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            request_fingerprint = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
        CREATE course_tutor_operation_lease:one SET
            course = course:one, course_version = course_version:one,
            session = course_tutor_session:one,
            operation = course_tutor_operation:one,
            lease_token = 'lease-one', expires_at = time::now() + 1h;
        CREATE course_tutor_turn:one SET
            course = course:one, course_version = course_version:one,
            session = course_tutor_session:one, chapter_key = 'limits',
            operation_key = 'message-one', turn_no = 1,
            role = 'assistant', content = 'Grounded';
        CREATE course_draft_revision:one SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'limits', revision_no = 1,
            base_artifact_hash = 'a', artifact_hash = 'b', operation = {};
        """
    )

    mastery_rows = cast(
        list[dict[str, Any]],
        await database.query(
            "SELECT pending_transfers FROM course_concept_mastery:one;"
        ),
    )
    assert mastery_rows[0]["pending_transfers"] == [{
        "chapter_key": "limits",
        "concept_key": "limit",
        "exercise_key": "core",
        "source_attempt_key": "attempt-one",
        "transfer_task_key": "core-transfer",
    }]
    turn_rows = cast(
        list[dict[str, Any]],
        await database.query(
            "SELECT operation_key FROM course_tutor_turn:one;"
        ),
    )
    assert turn_rows[0]["operation_key"] == "message-one"

    await database.query("DELETE course_tutor_session:one;")
    assert await empty(database, "course_tutor_operation_lease")
    assert await empty(database, "course_tutor_operation")
    assert await empty(database, "course_tutor_turn")

    await database.query(
        """
        CREATE course_tutor_session:two SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'limits', model_selection = {};
        CREATE course_tutor_operation:two SET
            course = course:one, course_version = course_version:one,
            session = course_tutor_session:two, chapter_key = 'limits',
            operation_identity = 'tutor-message-two',
            operation_key = 'tutor-message-two-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
            request_fingerprint = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
        CREATE course_tutor_operation_lease:two SET
            course = course:one, course_version = course_version:one,
            session = course_tutor_session:two,
            operation = course_tutor_operation:two,
            lease_token = 'lease-two', expires_at = time::now() + 1h;
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
        "course_tutor_operation_lease",
        "course_tutor_operation",
        "course_tutor_turn",
        "course_draft_revision",
    ):
        assert await empty(database, table), table
    await database.close()


@pytest.mark.asyncio
async def test_course_delete_cascades_to_legacy_lab_attempts() -> None:
    database = AsyncSurreal("mem://")
    await database.use("course_v2_attempt_cascade", "course_v2_attempt_cascade")
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
        CREATE lab:one SET
            course_version = course_version:one, chapter = chapter:one,
            lab_type = 'function_plot', payload = {};
        CREATE attempt:legacy SET
            lab = lab:one, answers = { value: '4' }, status = 'passed';
        """
    )

    await database.query("DELETE course:one;")

    assert await empty(database, "lab")
    assert await empty(database, "attempt")
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
        "course_tutor_operation_lease",
        "course_tutor_operation",
        "course_tutor_turn",
        "course_draft_revision",
        "course_export",
    ):
        assert f"{table}_course_idx" in up
