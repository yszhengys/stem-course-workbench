from pathlib import Path

from open_notebook.database.async_migrate import AsyncMigrationManager

V2_TABLES = (
    "course_exercise",
    "course_learning_event",
    "course_concept_mastery",
    "course_tutor_session",
    "course_tutor_turn",
    "course_draft_revision",
    "course_export",
)


def test_migration_26_is_registered_after_25() -> None:
    manager = AsyncMigrationManager()

    assert len(manager.up_migrations) == 26
    assert len(manager.down_migrations) == 26
    assert "course_exercise" in manager.up_migrations[-1].sql
    assert "REMOVE TABLE IF EXISTS course_exercise" in manager.down_migrations[-1].sql


def test_migration_26_is_additive_and_down_preserves_v1_tables() -> None:
    up = Path("open_notebook/database/migrations/26.surrealql").read_text()
    down = Path("open_notebook/database/migrations/26_down.surrealql").read_text()

    for table in V2_TABLES:
        assert f"DEFINE TABLE IF NOT EXISTS {table}" in up
        assert f"REMOVE TABLE IF EXISTS {table};" in down
        assert f"delete {table} where course == $before.id" in up

    for v1_table in (
        "course",
        "course_version",
        "chapter",
        "evidence",
        "lab",
        "attempt",
        "progress",
        "course_note",
        "course_evidence_anchor",
        "course_generation_run",
        "course_validation_finding",
    ):
        assert f"REMOVE TABLE IF EXISTS {v1_table};" not in down

    assert "DEFINE EVENT OVERWRITE course_delete ON TABLE course" in up
    assert "DEFINE EVENT OVERWRITE course_delete ON TABLE course" in down
    assert "delete course_evidence_anchor where course == $before.id" in down
