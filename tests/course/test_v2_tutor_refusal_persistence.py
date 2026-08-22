from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from surrealdb import AsyncSurreal

from open_notebook.course.v2_models import CourseTutorTurn


@pytest.mark.asyncio
async def test_insufficient_evidence_refusal_round_trips_through_migration_26(
    monkeypatch,
) -> None:
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("course_v2_refusal", "course_v2_refusal")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    await database.query(
        """
        DEFINE TABLE course SCHEMALESS;
        DEFINE TABLE course_version SCHEMALESS;
        DEFINE TABLE chapter SCHEMALESS;
        CREATE course:one;
        CREATE course_version:one;
        CREATE chapter:one;
        """
    )
    await database.query(
        Path("open_notebook/database/migrations/26.surrealql").read_text()
    )
    await database.query(
        """
        CREATE course_tutor_session:one SET
            course = course:one,
            course_version = course_version:one,
            chapter = chapter:one,
            chapter_key = 'limits',
            model_selection = {};
        """
    )
    refusal = CourseTutorTurn(
        course="course:one",
        course_version="course_version:one",
        session="course_tutor_session:one",
        chapter_key="limits",
        turn_no=1,
        role="assistant",
        content="The selected evidence is insufficient.",
        anchor_ids=(),
        insufficient_evidence=True,
    )

    await refusal.save()

    assert refusal.id is not None
    rows = await database.query(
        f"SELECT insufficient_evidence FROM {refusal.id};"
    )
    assert rows == [{"insufficient_evidence": True}]
    await database.close()
