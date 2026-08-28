from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from surrealdb import AsyncSurreal

from open_notebook.course.models import CourseEvidenceAnchor
from open_notebook.database.async_migrate import AsyncMigrationManager


def migration_sql(version: str) -> str:
    return Path(
        f"open_notebook/database/migrations/{version}.surrealql"
    ).read_text(encoding="utf-8")


def test_migration_30_is_registered_and_only_adds_visual_preview_fields() -> None:
    manager = AsyncMigrationManager()
    up = migration_sql("30")
    down = migration_sql("30_down")

    assert len(manager.up_migrations) >= 30
    assert len(manager.down_migrations) >= 30
    assert manager.up_migrations[29].sql == " ".join(
        line.strip()
        for line in up.splitlines()
        if line.strip() and not line.strip().startswith("--")
    )
    for field in ("visual_preview_path", "visual_preview_status"):
        assert (
            f"DEFINE FIELD IF NOT EXISTS {field} ON TABLE course_evidence_anchor"
            in up
        )
        assert (
            f"REMOVE FIELD IF EXISTS {field} ON TABLE course_evidence_anchor"
            in down
        )
    assert "REMOVE TABLE" not in down


@pytest.mark.asyncio
async def test_migration_30_round_trip_preserves_legacy_anchor() -> None:
    database = AsyncSurreal("mem://")
    await database.use("course_v2_migration_30", "course_v2_migration_30")
    try:
        await database.query(
            "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
        )
        for version in ("24", "25", "26", "27", "28", "29"):
            await database.query(migration_sql(version))
        await database.query(
            """
            CREATE notebook:one SET name = 'Notebook';
            CREATE source:one SET title = 'Slides';
            CREATE course:one SET
                title = 'Geometry', notebook = notebook:one,
                status = 'draft', language = 'en',
                source_ids = [source:one], primary_source_ids = [source:one],
                supplement_source_ids = [];
            CREATE course_evidence_anchor:one SET
                course = course:one, source = source:one,
                anchor_id = 'anchor:one',
                locator = {
                    source_id: 'source:one', kind: 'pptx_slide', index: 1,
                    block_key: '#/texts/0', quote: 'Triangle diagram',
                    content_sha256: $hash, bbox: [0.1, 0.2, 0.5, 0.6]
                },
                quote_sha256 = $quote_hash, source_role = 'PRIMARY',
                preview_path = 'cache/slide-0001-a.svg', is_current = true;
            """,
            {"hash": "a" * 64, "quote_hash": "b" * 64},
        )
        before = cast(
            dict[str, Any],
            await database.query(
                "SELECT * OMIT created, updated FROM ONLY course_evidence_anchor:one;"
            ),
        )

        await database.query(migration_sql("30"))
        migrated = CourseEvidenceAnchor(
            **cast(
                dict[str, Any],
                await database.query(
                    "SELECT * FROM ONLY course_evidence_anchor:one;"
                ),
            )
        )
        assert migrated.visual_preview_status == "text_only"
        assert migrated.visual_preview_path is None

        await database.query(
            """
            UPDATE course_evidence_anchor:one SET
                visual_preview_status = 'available',
                visual_preview_path = 'cache/slide-0001-a.png';
            """
        )
        available = CourseEvidenceAnchor(
            **cast(
                dict[str, Any],
                await database.query(
                    "SELECT * FROM ONLY course_evidence_anchor:one;"
                ),
            )
        )
        assert available.visual_preview_status == "available"

        await database.query(migration_sql("30_down"))
        downgraded = cast(
            dict[str, Any],
            await database.query(
                "SELECT * OMIT created, updated FROM ONLY course_evidence_anchor:one;"
            ),
        )
        assert downgraded == before
    finally:
        await database.close()
