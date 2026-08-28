from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from surrealdb import AsyncSurreal

from open_notebook.course.models import BibliographicSource
from open_notebook.database.async_migrate import AsyncMigrationManager


def migration_sql(version: str) -> str:
    return Path(
        f"open_notebook/database/migrations/{version}.surrealql"
    ).read_text(encoding="utf-8")


def test_migration_31_is_registered_and_isolates_course_bibliography() -> None:
    manager = AsyncMigrationManager()
    up = migration_sql("31")
    down = migration_sql("31_down")

    assert len(manager.up_migrations) == 31
    assert len(manager.down_migrations) == 31
    assert manager.up_migrations[-1].sql == " ".join(
        line.strip()
        for line in up.splitlines()
        if line.strip() and not line.strip().startswith("--")
    )
    assert "DEFINE TABLE IF NOT EXISTS course_bibliographic_source SCHEMAFULL" in up
    assert (
        "FIELDS course, source UNIQUE" in up
        and "course_bibliography_identity_unique" in up
    )
    for field in (
        "course",
        "source",
        "source_role",
        "authors",
        "title",
        "edition",
        "publisher",
        "year",
        "doi",
        "isbn",
        "license",
        "manually_reviewed",
    ):
        assert (
            f"DEFINE FIELD IF NOT EXISTS {field} ON TABLE course_bibliographic_source"
            in up
        )
    assert "course_bibliography_course_delete" in up
    assert "course_bibliography_source_delete" in up
    assert "REMOVE TABLE IF EXISTS course_bibliographic_source" in down
    assert all(
        "ON TABLE course_bibliographic_source" in line
        for line in up.splitlines()
        if line.strip().startswith("DEFINE FIELD")
    )


@pytest.mark.asyncio
async def test_migration_31_round_trip_and_deletion_cascade() -> None:
    database = AsyncSurreal("mem://")
    await database.use("course_v2_migration_31", "course_v2_migration_31")
    try:
        await database.query(
            "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
        )
        for version in ("24", "25", "26", "27", "28", "29", "30"):
            await database.query(migration_sql(version))
        await database.query(
            """
            CREATE notebook:one SET name = 'Notebook';
            CREATE source:one SET title = 'Textbook';
            CREATE course:one SET
                title = 'Geometry', notebook = notebook:one,
                status = 'draft', language = 'en',
                source_ids = [source:one], primary_source_ids = [source:one],
                supplement_source_ids = [];
            """
        )
        before = cast(
            dict[str, Any],
            await database.query(
                "SELECT * OMIT created, updated FROM ONLY course:one;"
            ),
        )

        await database.query(migration_sql("31"))
        await database.query(
            """
            CREATE course_bibliographic_source:one SET
                course = course:one, source = source:one,
                source_role = 'PRIMARY', authors = ['Ada Lovelace'],
                title = 'Geometry', manually_reviewed = true;
            """
        )
        row = cast(
            dict[str, Any],
            await database.query(
                "SELECT * FROM ONLY course_bibliographic_source:one;"
            ),
        )
        assert BibliographicSource(**row).manually_reviewed is True

        await database.query(migration_sql("31_down"))
        after = cast(
            dict[str, Any],
            await database.query(
                "SELECT * OMIT created, updated FROM ONLY course:one;"
            ),
        )
        assert after == before

        await database.query(migration_sql("31"))
        await database.query(
            """
            CREATE course_bibliographic_source:two SET
                course = course:one, source = source:one,
                source_role = 'PRIMARY', authors = [],
                manually_reviewed = false;
            DELETE course:one;
            """
        )
        assert await database.query(
            "SELECT * FROM course_bibliographic_source;"
        ) == []
        assert cast(
            dict[str, Any],
            await database.query("SELECT * FROM ONLY source:one;"),
        )["title"] == "Textbook"

        await database.query(
            """
            CREATE source:two SET title = 'Slides';
            CREATE course:two SET
                title = 'Vectors', notebook = notebook:one,
                status = 'draft', language = 'en',
                source_ids = [source:two], primary_source_ids = [source:two],
                supplement_source_ids = [];
            CREATE course_bibliographic_source:three SET
                course = course:two, source = source:two,
                source_role = 'PRIMARY', authors = [],
                manually_reviewed = false;
            DELETE source:two;
            """
        )
        assert await database.query(
            "SELECT * FROM course_bibliographic_source;"
        ) == []
        assert cast(
            dict[str, Any],
            await database.query("SELECT * FROM ONLY course:two;"),
        )["title"] == "Vectors"
    finally:
        await database.close()
