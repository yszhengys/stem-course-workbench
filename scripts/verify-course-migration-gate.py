#!/usr/bin/env python3
"""Verify Course migrations 1–31 against a temporary persistent SurrealDB."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, cast

from open_notebook.database.async_migrate import (
    AsyncMigration,
    AsyncMigrationManager,
    get_latest_version,
)
from open_notebook.database.repository import db_connection, parse_record_ids

LEGACY_RECORDS = (
    "source:migration_gate_source",
    "notebook:migration_gate_notebook",
    "course:migration_gate_course",
    "course_version:migration_gate_version",
    "chapter:migration_gate_chapter",
    "lab:migration_gate_lab",
    "attempt:migration_gate_attempt",
    "progress:migration_gate_progress",
    "course_note:migration_gate_note",
)

SEED_SQL = """
BEGIN TRANSACTION;
CREATE ONLY source:migration_gate_source CONTENT {
    title: 'Migration gate source',
    full_text: 'Original V1 source record'
};
CREATE ONLY notebook:migration_gate_notebook CONTENT {
    name: 'Migration gate notebook',
    archived: false
};
CREATE ONLY course:migration_gate_course CONTENT {
    title: 'Migration gate physics course',
    subject: 'physics',
    status: 'draft',
    notebook: notebook:migration_gate_notebook,
    language: 'zh-CN',
    source_ids: [source:migration_gate_source],
    primary_source_ids: [source:migration_gate_source],
    supplement_source_ids: []
};
CREATE ONLY course_version:migration_gate_version CONTENT {
    course: course:migration_gate_course,
    version_no: 1,
    status: 'draft',
    outline_hash: 'migration-gate-outline'
};
CREATE ONLY chapter:migration_gate_chapter CONTENT {
    course_version: course_version:migration_gate_version,
    chapter_no: 1,
    chapter_key: 'vectors',
    version_no: 1,
    title: 'Vectors',
    content: 'Persistent V1 chapter',
    review_status: 'pending',
    validation_status: 'pending',
    status: 'draft'
};
CREATE ONLY lab:migration_gate_lab CONTENT {
    course_version: course_version:migration_gate_version,
    chapter: chapter:migration_gate_chapter,
    lab_type: 'vector_plot',
    prompt: 'Inspect a bounded vector',
    payload: { kind: 'vector', x: 3, y: 4 },
    answer: { magnitude: 5 }
};
CREATE ONLY attempt:migration_gate_attempt CONTENT {
    lab: lab:migration_gate_lab,
    answers: { magnitude: 5 },
    status: 'passed',
    result: { correct: true },
    course: course:migration_gate_course,
    course_version: course_version:migration_gate_version,
    chapter: chapter:migration_gate_chapter,
    chapter_key: 'vectors',
    exercise_key: 'vector-magnitude',
    answer: '5',
    hints_used: 0,
    answer_revealed: false,
    transfer_completed: true,
    orphan_status: 'active'
};
CREATE ONLY progress:migration_gate_progress CONTENT {
    course: course:migration_gate_course,
    chapter: chapter:migration_gate_chapter,
    chapter_key: 'vectors',
    block_key: 'definition-vector',
    status: 'completed',
    orphan_status: 'active'
};
CREATE ONLY course_note:migration_gate_note CONTENT {
    course: course:migration_gate_course,
    chapter: chapter:migration_gate_chapter,
    chapter_key: 'vectors',
    block_key: 'definition-vector',
    content: 'Persistent learner note',
    orphan_status: 'active'
};
COMMIT TRANSACTION;
"""


async def _query(sql: str) -> Any:
    async with db_connection() as connection:
        result = parse_record_ids(await connection.query(sql))
    if isinstance(result, str):
        raise RuntimeError(result)
    return result


async def _database_tables() -> set[str]:
    info = await _query("INFO FOR DB;")
    if not isinstance(info, dict) or not isinstance(info.get("tables"), dict):
        raise AssertionError(f"Unexpected INFO FOR DB response: {info!r}")
    return set(info["tables"])


async def _table_fields(table: str) -> set[str]:
    if not table.replace("_", "").isalnum():
        raise ValueError(f"Unsafe table name: {table!r}")
    info = await _query(f"INFO FOR TABLE {table};")
    if not isinstance(info, dict) or not isinstance(info.get("fields"), dict):
        raise AssertionError(f"Unexpected INFO FOR TABLE {table}: {info!r}")
    return set(info["fields"])


async def _record(record_id: str) -> dict[str, Any]:
    if record_id not in LEGACY_RECORDS:
        raise ValueError(f"Unexpected migration-gate record: {record_id}")
    result = await _query(f"SELECT * FROM ONLY {record_id};")
    if not isinstance(result, dict):
        raise AssertionError(f"Missing legacy record {record_id}: {result!r}")
    return cast(dict[str, Any], result)


async def _migrate_to(target_version: int) -> None:
    manager = AsyncMigrationManager()
    current_version = await get_latest_version()
    if not 0 <= target_version <= len(manager.up_migrations):
        raise AssertionError(f"Invalid target migration version {target_version}")
    while current_version < target_version:
        await manager.up_migrations[current_version].run(
            current_version=current_version,
            target_version=current_version + 1,
        )
        current_version += 1
    while current_version > target_version:
        await manager.down_migrations[current_version - 1].run(
            current_version=current_version,
            target_version=current_version - 1,
        )
        current_version -= 1
    actual_version = await get_latest_version()
    if actual_version != target_version:
        raise AssertionError(
            f"Expected migration version {target_version}, got {actual_version}"
        )


async def _assert_legacy_records() -> None:
    records = {record_id: await _record(record_id) for record_id in LEGACY_RECORDS}
    assert records["source:migration_gate_source"]["title"] == "Migration gate source"
    assert records["course:migration_gate_course"]["title"] == (
        "Migration gate physics course"
    )
    assert records["course_version:migration_gate_version"]["version_no"] == 1
    assert records["chapter:migration_gate_chapter"]["chapter_key"] == "vectors"
    assert records["lab:migration_gate_lab"]["payload"]["x"] == 3
    assert records["attempt:migration_gate_attempt"]["answer"] == "5"
    assert records["progress:migration_gate_progress"]["status"] == "completed"
    assert records["course_note:migration_gate_note"]["content"] == (
        "Persistent learner note"
    )


async def _assert_v2_schema_present() -> None:
    tables = await _database_tables()
    assert {
        "course_exercise",
        "course_learning_event",
        "course_concept_mastery",
        "course_tutor_session",
        "course_bibliographic_source",
    } <= tables
    assert {"proposal_hash", "approved_hash", "approval_reason"} <= await _table_fields(
        "lab"
    )
    assert {"visual_preview_path", "visual_preview_status"} <= await _table_fields(
        "course_evidence_anchor"
    )
    assert {
        "upgrade_source_version",
        "upgrade_idempotency_key",
        "upgrade_confirmation",
    } <= await _table_fields("course_version")


async def _assert_v2_schema_absent() -> None:
    tables = await _database_tables()
    assert "course_exercise" not in tables
    assert "course_bibliographic_source" not in tables
    assert "proposal_hash" not in await _table_fields("lab")
    assert "visual_preview_path" not in await _table_fields("course_evidence_anchor")
    assert "upgrade_source_version" not in await _table_fields("course_version")


async def _assert_failed_probe_rolls_back() -> None:
    probe = AsyncMigration(
        "DEFINE TABLE atomic_release_probe SCHEMALESS; "
        "CREATE atomic_release_probe:must_rollback SET marker = 'rollback'; "
        "THROW 'intentional release-gate rollback';"
    )
    try:
        await probe.run(current_version=31, target_version=32)
    except Exception:
        pass
    else:
        raise AssertionError("The intentionally failing migration unexpectedly succeeded")
    assert await get_latest_version() == 31
    assert "atomic_release_probe" not in await _database_tables()


async def seed_and_upgrade() -> None:
    if await get_latest_version() != 0:
        raise AssertionError("seed-up requires a fresh temporary database")
    await _migrate_to(25)
    await _query(SEED_SQL)
    await _assert_legacy_records()
    await _migrate_to(31)
    await _assert_legacy_records()
    await _assert_v2_schema_present()


async def restart_down_and_up() -> None:
    if await get_latest_version() != 31:
        raise AssertionError("restart phase expected persistent migration version 31")
    await _assert_legacy_records()
    await _assert_v2_schema_present()
    await _migrate_to(25)
    await _assert_legacy_records()
    await _assert_v2_schema_absent()
    await _migrate_to(31)
    await _assert_legacy_records()
    await _assert_v2_schema_present()
    await _assert_failed_probe_rolls_back()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("seed-up", "restart-down-up"),
        required=True,
    )
    arguments = parser.parse_args()
    if arguments.phase == "seed-up":
        asyncio.run(seed_and_upgrade())
    else:
        asyncio.run(restart_down_and_up())


if __name__ == "__main__":
    main()
