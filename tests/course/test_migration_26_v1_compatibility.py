from pathlib import Path
from typing import Any, Protocol

import pytest
from surrealdb import AsyncSurreal


class QueryDatabase(Protocol):
    async def query(self, sql: str) -> Any: ...

V1_TABLES = (
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
)


def migration_sql(version: str) -> str:
    return Path(
        f"open_notebook/database/migrations/{version}.surrealql"
    ).read_text()


async def snapshot(database: QueryDatabase) -> dict[str, object]:
    return {
        table: await database.query(f"SELECT * OMIT created, updated FROM {table};")
        for table in V1_TABLES
    }


@pytest.mark.asyncio
async def test_migration_26_round_trip_preserves_complete_v1_aggregate() -> None:
    database = AsyncSurreal("mem://")
    await database.use("course_v2_v1_compat", "course_v2_v1_compat")
    await database.query(
        "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
    )
    await database.query(migration_sql("24"))
    await database.query(migration_sql("25"))
    await database.query(
        """
        CREATE notebook:one SET name = 'V1 Notebook';
        CREATE source:one SET title = 'V1 Source';
        CREATE course:one SET
            title = 'V1 Mechanics', notebook = notebook:one,
            subject = 'physics', status = 'ready', language = 'zh-CN',
            source_ids = [source:one], primary_source_ids = [source:one],
            supplement_source_ids = [];
        CREATE course_version:one SET
            course = course:one, version_no = 1, status = 'published',
            outline_hash = 'outline-hash';
        CREATE chapter:one SET
            course_version = course_version:one, chapter_no = 1,
            chapter_key = 'motion', version_no = 1,
            title = 'Motion', status = 'published',
            review_status = 'passed', validation_status = 'passed';
        CREATE evidence:one SET
            course = course:one, source = source:one, kind = 'pdf',
            status = 'ready', source_role = 'PRIMARY', source_hash = 'source-hash';
        CREATE lab:one SET
            course_version = course_version:one, chapter = chapter:one,
            lab_type = 'kinematics', payload = { kind: 'kinematics' };
        CREATE attempt:one SET
            lab = lab:one, answers = { value: '9.8' }, status = 'passed',
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'motion',
            exercise_key = 'motion-core', answer = '9.8', hints_used = 0,
            answer_revealed = false, transfer_completed = true,
            orphan_status = 'active';
        CREATE progress:one SET
            course = course:one, chapter = chapter:one,
            chapter_key = 'motion', block_key = 'section-1',
            orphan_status = 'active', status = 'completed';
        CREATE course_note:one SET
            course = course:one, chapter = chapter:one,
            chapter_key = 'motion', block_key = 'section-1',
            orphan_status = 'active', content = 'V1 note';
        CREATE course_evidence_anchor:one SET
            course = course:one, source = source:one, evidence = evidence:one,
            anchor_id = 'anchor:one',
            locator = {
                source_id: 'source:one', kind: 'pdf_page', index: 1,
                block_key: 'block-1', quote: 'Grounded quote',
                content_sha256: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            },
            quote_sha256 = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
            source_role = 'PRIMARY', is_current = true;
        CREATE course_generation_run:one SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'motion',
            stage = 'chapter_content', adapter = 'codex_cli',
            model = 'gpt-5.6-sol', status = 'succeeded',
            prompt_version = 'v1', input_hash = 'input-hash',
            output_hash = 'output-hash';
        CREATE course_validation_finding:one SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, generation_run = course_generation_run:one,
            chapter_key = 'motion', finding = { code: 'warning' },
            severity = 'warning', status = 'acknowledged';
        """
    )
    before = await snapshot(database)

    await database.query(migration_sql("26"))
    await database.query(migration_sql("26_down"))

    assert await snapshot(database) == before
    await database.close()
