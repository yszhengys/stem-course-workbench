from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, cast
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from surrealdb import AsyncSurreal

from api.models import (
    CourseLearningUpgradeRequest,
    CourseLearningUpgradeResponse,
)
from open_notebook.course.authoring_service import (
    AuthoringService,
    LearningUpgradeConflictError,
)
from open_notebook.course.contracts import (
    ChapterArtifact,
    ChapterSection,
    CourseOutlineArtifact,
)
from open_notebook.course.models import CourseVersion
from open_notebook.course.workflow_service import _artifact_hash
from open_notebook.database.repository import ensure_record_id


@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app)


def _migration(version: str) -> str:
    return Path(
        f"open_notebook/database/migrations/{version}.surrealql"
    ).read_text(encoding="utf-8")


def _outline() -> CourseOutlineArtifact:
    return CourseOutlineArtifact(
        title="Legacy Calculus",
        chapters=[
            {
                "key": "limits",
                "title": "Limits",
                "purpose": "Understand limits.",
                "objective_keys": ["limit-laws"],
                "anchor_ids": ["anchor:limits"],
                "lab_keys": ["limit-plot"],
            }
        ],
        concepts=[
            {
                "key": "limit-laws",
                "label": "Limit laws",
                "anchor_ids": ["anchor:limits"],
            }
        ],
    )


def _artifact() -> ChapterArtifact:
    return ChapterArtifact(
        chapter_key="limits",
        purpose="Understand limits.",
        objectives=["Apply limit laws."],
        sections=[
            ChapterSection(
                key="definition",
                title="Definition",
                markdown="A limit describes nearby behavior.",
                anchor_ids=["anchor:limits"],
                provenance="adapted",
            )
        ],
        citations=["anchor:limits"],
        attributions={
            "purpose": {
                "provenance": "adapted",
                "anchor_ids": ["anchor:limits"],
            },
            "prerequisites": [],
            "objectives": [
                {
                    "provenance": "adapted",
                    "anchor_ids": ["anchor:limits"],
                }
            ],
            "definitions": [],
            "misconceptions": [],
            "pitfalls": [],
            "quick_reference": [],
        },
    )


async def _database(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Any]:
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("learning_upgrade", "learning_upgrade")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    await database.query(
        "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
    )
    for version in ("24", "25", "26", "27", "28"):
        await database.query(_migration(version))
    try:
        yield database
    finally:
        await database.close()


async def _seed_published_course(database: Any) -> None:
    outline = _outline().model_dump(mode="json")
    artifact = _artifact().model_dump(mode="json")
    await database.query(
        """
        CREATE notebook:one SET name = 'Notebook';
        CREATE course:one SET
            title = 'Legacy Calculus', notebook = notebook:one,
            subject = 'mathematics', language = 'zh-CN', status = 'ready',
            source_ids = [], primary_source_ids = [], supplement_source_ids = [],
            outline_version_id = course_version:published,
            outline = $outline;
        CREATE course_version:published SET
            course = course:one, version_no = 1, status = 'published',
            outline_hash = $outline_hash, outline_artifact = $outline,
            input_hash = 'source-outline', approved_at = d'2026-08-01T00:00:00Z',
            confirmation = '确认大纲', published_at = d'2026-08-02T00:00:00Z';
        CREATE chapter:published SET
            course_version = course_version:published, chapter_no = 1,
            chapter_key = 'limits', version_no = 2, title = 'Limits',
            artifact = $artifact, content = 'legacy content',
            citations = [{ anchor_id: 'anchor:limits' }],
            input_hash = 'source-chapter', status = 'published',
            review_status = 'passed', validation_status = 'passed',
            published_at = d'2026-08-02T00:00:00Z';
        CREATE course_note:old SET
            course = course:one, chapter = chapter:published,
            chapter_key = 'limits', block_key = 'definition',
            content = 'Keep this note.', orphan_status = 'active';
        CREATE lab:old SET
            course_version = course_version:published,
            chapter = chapter:published, lab_type = 'function_plot',
            prompt = 'Explore the limit.',
            payload = { key: 'limit-plot', kind: 'function_plot' },
            answer = NONE;
        CREATE course_learning_event:old SET
            course = course:one, course_version = course_version:published,
            chapter = chapter:published, chapter_key = 'limits',
            concept_key = 'limit-laws', event_key = 'old-event',
            kind = 'chapter_opened', payload = {},
            occurred_at = d'2026-08-03T00:00:00Z';
        CREATE course_concept_mastery:old SET
            course = course:one, course_version = course_version:published,
            chapter_key = 'limits', concept_key = 'limit-laws',
            status = 'practiced', successful_exercise_keys = [],
            unrevealed_success_count = 0, pending_transfers = [],
            review_level = 0, snapshot_hash = 'legacy-snapshot';
        CREATE course_tutor_session:old SET
            course = course:one, course_version = course_version:published,
            chapter = chapter:published, chapter_key = 'limits',
            model_selection = { adapter: 'codex_cli', model: 'test' },
            status = 'active';
        CREATE course_exercise:old SET
            course = course:one, course_version = course_version:published,
            chapter = chapter:published, chapter_key = 'limits',
            exercise_key = 'legacy-core',
            blueprint = { key: 'legacy-core', chapter_key: 'limits' },
            source_anchor_ids = ['anchor:limits'], difficulty = {},
            grader = { kind: 'numeric', expected: '4' },
            is_core = true, is_gating = true, is_source_level = true,
            verification = {
                level: 'L1', method: 'self_consistency', anchor_ids: []
            }, review_run_ids = [];
        """,
        {
            "outline": outline,
            "outline_hash": _artifact_hash(outline),
            "artifact": artifact,
        },
    )


@pytest.mark.asyncio
async def test_learning_upgrade_clones_only_current_published_authoring_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async for database in _database(monkeypatch):
        await _seed_published_course(database)
        assert len(
            await database.query(
                "SELECT id FROM lab WHERE course_version = course_version:published;"
            )
        ) == 1
        assert len(await CourseVersion.labs("course_version:published")) == 1
        old_state = await database.query(
            """
            SELECT * OMIT created, updated FROM course_note:old;
            SELECT * OMIT created FROM course_learning_event:old;
            SELECT * OMIT created, updated FROM course_tutor_session:old;
            """
        )

        result = await AuthoringService().prepare_learning_upgrade(
            course_id="course:one",
            confirmation="创建学习升级版本",
            idempotency_key="upgrade-old-learning",
        )

        assert result.source_version_id == "course_version:published"
        assert result.version.version_no == 2
        assert result.version.status == "generating"
        assert result.version.upgrade_source_version == "course_version:published"
        assert result.version.upgrade_confirmation == "创建学习升级版本"
        assert result.version.upgrade_idempotency_key == "upgrade-old-learning"
        assert result.version.confirmation == "确认大纲"
        assert len(result.chapters) == 1
        cloned = result.chapters[0]
        assert cloned.chapter_key == "limits"
        assert cloned.status == "ready"
        assert cloned.version_no == 1
        assert cloned.artifact == _artifact().model_dump(mode="json")
        assert cloned.input_hash is not None
        assert cloned.published_at is None

        course = cast(
            dict[str, Any], await database.query("SELECT * FROM ONLY course:one;")
        )
        assert str(course["outline_version_id"]) == str(result.version.id)
        assert course["status"] == "generating"

        for table in (
            "course_exercise",
            "course_learning_event",
            "course_concept_mastery",
            "course_tutor_session",
        ):
            rows = await database.query(
                f"SELECT id FROM {table} WHERE course_version = $version;",
                    {"version": ensure_record_id(str(result.version.id))},
            )
            assert rows == []
        assert await database.query(
            "SELECT id FROM course_note WHERE chapter = $chapter;",
            {"chapter": ensure_record_id(str(cloned.id))},
        ) == []
        cloned_labs = cast(
            list[dict[str, Any]],
            await database.query(
                "SELECT * FROM lab WHERE course_version = $version;",
                {"version": ensure_record_id(str(result.version.id))},
            ),
        )
        all_labs = await database.query("SELECT * FROM lab ORDER BY id;")
        assert len(cloned_labs) == 1, all_labs
        assert str(cloned_labs[0]["chapter"]) == str(cloned.id)
        assert cloned_labs[0]["payload"]["key"] == "limit-plot"
        lineage = cast(
            list[dict[str, Any]],
            await database.query(
                """
                SELECT * FROM course_generation_run
                WHERE course_version = $version AND stage = 'chapter_content';
                """,
                {"version": ensure_record_id(str(result.version.id))},
            ),
        )
        assert len(lineage) == 1
        assert lineage[0]["status"] == "succeeded"
        assert str(lineage[0]["chapter"]) == str(cloned.id)
        assert await database.query(
            """
            SELECT * OMIT created, updated FROM course_note:old;
            SELECT * OMIT created FROM course_learning_event:old;
            SELECT * OMIT created, updated FROM course_tutor_session:old;
            """
        ) == old_state


@pytest.mark.asyncio
async def test_learning_upgrade_is_idempotent_and_rejects_a_different_active_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async for database in _database(monkeypatch):
        await _seed_published_course(database)
        service = AuthoringService()

        first = await service.prepare_learning_upgrade(
            course_id="course:one",
            confirmation="创建学习升级版本",
            idempotency_key="same-upgrade",
        )
        replay = await service.prepare_learning_upgrade(
            course_id="course:one",
            confirmation="创建学习升级版本",
            idempotency_key="same-upgrade",
        )

        assert replay.version.id == first.version.id
        assert [chapter.id for chapter in replay.chapters] == [
            chapter.id for chapter in first.chapters
        ]
        assert len(await database.query("SELECT id FROM course_version;")) == 2
        assert len(await database.query("SELECT id FROM chapter;")) == 2

        with pytest.raises(LearningUpgradeConflictError, match="active"):
            await service.prepare_learning_upgrade(
                course_id="course:one",
                confirmation="创建学习升级版本",
                idempotency_key="different-upgrade",
            )
        assert len(await database.query("SELECT id FROM course_version;")) == 2


@pytest.mark.asyncio
async def test_learning_upgrade_requires_the_exact_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async for database in _database(monkeypatch):
        await _seed_published_course(database)

        with pytest.raises(ValueError, match="创建学习升级版本"):
            await AuthoringService().prepare_learning_upgrade(
                course_id="course:one",
                confirmation="确认升级",
                idempotency_key="wrong-confirmation",
            )
        assert len(await database.query("SELECT id FROM course_version;")) == 1


def test_learning_upgrade_request_is_strict_and_requires_exact_confirmation() -> None:
    with pytest.raises(ValidationError):
        CourseLearningUpgradeRequest(
            confirmation="确认升级",
            idempotency_key="upgrade-one",
        )
    with pytest.raises(ValidationError):
        CourseLearningUpgradeRequest.model_validate(
            {
                "confirmation": "创建学习升级版本",
                "idempotency_key": "upgrade-one",
                "source_version_id": "course_version:untrusted",
            }
        )


def test_learning_upgrade_route_returns_the_new_build_version(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.routers.course as router_module

    prepare = AsyncMock(
        return_value=CourseLearningUpgradeResponse(
            course_id="course:one",
            source_version_id="course_version:published",
            version_id="course_version:upgrade",
            version_no=2,
            status="generating",
            chapter_keys=("limits",),
        )
    )
    monkeypatch.setattr(
        router_module.course_v2_service,
        "prepare_learning_upgrade",
        prepare,
        raising=False,
    )

    response = client.post(
        "/api/courses/course:one/versions/prepare-learning-upgrade",
        json={
            "confirmation": "创建学习升级版本",
            "idempotency_key": "upgrade-one",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "course_id": "course:one",
        "source_version_id": "course_version:published",
        "version_id": "course_version:upgrade",
        "version_no": 2,
        "status": "generating",
        "chapter_keys": ["limits"],
    }
    prepare.assert_awaited_once_with(
        "course:one",
        CourseLearningUpgradeRequest(
            confirmation="创建学习升级版本",
            idempotency_key="upgrade-one",
        ),
    )
