from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from surrealdb import AsyncSurreal

from open_notebook.course.models import Lab
from open_notebook.database.async_migrate import AsyncMigrationManager


def migration_sql(version: str) -> str:
    return Path(
        f"open_notebook/database/migrations/{version}.surrealql"
    ).read_text(encoding="utf-8")


def test_migration_29_is_registered_and_only_adds_lab_approval_fields() -> None:
    manager = AsyncMigrationManager()
    up = migration_sql("29")
    down = migration_sql("29_down")

    assert len(manager.up_migrations) >= 29
    assert len(manager.down_migrations) >= 29
    assert manager.up_migrations[28].sql == " ".join(
        line.strip()
        for line in up.splitlines()
        if line.strip() and not line.strip().startswith("--")
    )
    for field in (
        "proposal_hash",
        "approved_hash",
        "approved_at",
        "approval_reason",
    ):
        assert f"DEFINE FIELD IF NOT EXISTS {field} ON TABLE lab" in up
        assert f"REMOVE FIELD IF EXISTS {field} ON TABLE lab" in down
    assert "REMOVE TABLE" not in down


@pytest.mark.asyncio
async def test_migration_29_round_trip_preserves_legacy_lab_and_drops_approval() -> None:
    database = AsyncSurreal("mem://")
    await database.use("course_v2_migration_29", "course_v2_migration_29")
    try:
        await database.query(
            "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
        )
        for version in ("24", "25", "26", "27", "28"):
            await database.query(migration_sql(version))
        await database.query(
            """
            CREATE notebook:one SET name = 'Notebook';
            CREATE course:one SET
                title = 'Calculus', notebook = notebook:one,
                status = 'generating', language = 'en', source_ids = [],
                primary_source_ids = [], supplement_source_ids = [];
            CREATE course_version:one SET
                course = course:one, version_no = 1, status = 'generating';
            CREATE chapter:one SET
                course_version = course_version:one, chapter_no = 1,
                chapter_key = 'limits', version_no = 1, title = 'Limits',
                status = 'reviewing';
            CREATE lab:legacy SET
                course_version = course_version:one, chapter = chapter:one,
                lab_type = 'function_plot',
                payload = {
                    kind: 'function_plot', key: 'limit-plot', title: 'Plot',
                    expressions: ['x'], domain: {}, controls: [], objects: [],
                    anchor_ids: [], provenance: 'pedagogical'
                };
            """
        )
        before = cast(
            dict[str, Any],
            await database.query(
                "SELECT * OMIT created, updated FROM ONLY lab:legacy;"
            ),
        )

        await database.query(migration_sql("29"))
        migrated = cast(
            dict[str, Any],
            await database.query("SELECT * FROM ONLY lab:legacy;"),
        )
        legacy = Lab(**migrated)
        assert legacy.proposal_hash is None
        assert legacy.approved_hash is None
        assert legacy.approved_at is None
        assert legacy.approval_reason is None

        await database.query(
            """
            UPDATE lab:legacy SET
                proposal_hash = $hash,
                approved_hash = $hash,
                approved_at = d'2026-08-29T00:00:00Z',
                approval_reason = 'Checked the complete proposal.';
            """,
            {"hash": "a" * 64},
        )
        approved = Lab(
            **cast(
                dict[str, Any],
                await database.query("SELECT * FROM ONLY lab:legacy;"),
            )
        )
        assert approved.approved_hash == "a" * 64
        assert approved.approved_at is not None

        await database.query(migration_sql("29_down"))
        downgraded = cast(
            dict[str, Any],
            await database.query(
                "SELECT * OMIT created, updated FROM ONLY lab:legacy;"
            ),
        )
        assert downgraded == before
    finally:
        await database.close()
