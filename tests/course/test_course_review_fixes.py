import hashlib
import json
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from surrealdb import AsyncSurreal, RecordID

from api.course_service import (
    CourseConflictError,
    CourseImmutableError,
    CourseService,
)
from open_notebook.course.evidence_service import EvidenceService
from open_notebook.course.models import (
    Attempt,
    Chapter,
    Course,
    CourseGenerationRun,
    CourseNote,
    CourseVersion,
    Lab,
    Progress,
)
from open_notebook.course.workflow_service import artifact_replay_hash
from open_notebook.domain.notebook import Asset, Notebook, Source
from open_notebook.exceptions import InvalidInputError, NotFoundError


def test_migration_25_extends_and_down_restores_course_delete_event():
    up = Path("open_notebook/database/migrations/25.surrealql").read_text()
    down = Path("open_notebook/database/migrations/25_down.surrealql").read_text()

    assert "DEFINE EVENT OVERWRITE course_delete ON TABLE course" in up
    for table in (
        "course_version",
        "evidence",
        "progress",
        "course_note",
        "course_evidence_anchor",
        "course_generation_run",
        "course_validation_finding",
    ):
        assert f"delete {table} where course == $before.id" in up
    assert "DEFINE EVENT OVERWRITE course_delete ON TABLE course" in down
    assert "REMOVE EVENT IF EXISTS course_delete" not in down
    assert "delete course_version where course == $before.id" in down
    assert "delete course_evidence_anchor" not in down


def artifact_hash(artifact: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def approved_outline() -> dict:
    return {
        "title": "Calculus",
        "chapters": [
            {
                "key": "limits",
                "title": "Limits",
                "purpose": "Learn limits.",
                "objective_keys": ["limit"],
                "anchor_ids": ["anchor:one"],
            }
        ],
        "concepts": [
            {
                "key": "limit",
                "label": "Limit",
                "anchor_ids": ["anchor:one"],
            }
        ],
        "dependency_edges": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("version_status", ["published", "failed"])
async def test_outline_reapproval_rejects_terminal_version(monkeypatch, version_status):
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="outline_ready",
        outline_version_id="course_version:1",
    )
    version = CourseVersion(
        id="course_version:1",
        course="course:one",
        version_no=1,
        status=version_status,
        outline_artifact={"chapters": [{"key": "limits"}]},
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    version_save = AsyncMock()
    course_save = AsyncMock()
    monkeypatch.setattr(CourseVersion, "save", version_save)
    monkeypatch.setattr(Course, "save", course_save)

    with pytest.raises(CourseConflictError):
        await CourseService.approve_outline(
            "course:one", "course_version:1", "确认大纲"
        )

    version_save.assert_not_awaited()
    course_save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("version_status", ["draft", "failed"])
async def test_version_publish_rejects_illegal_state(monkeypatch, version_status):
    artifact = {"chapters": [{"key": "limits"}]}
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="ready",
        outline_version_id="course_version:1",
    )
    version = CourseVersion(
        id="course_version:1",
        course="course:one",
        version_no=1,
        status=version_status,
        outline_artifact=artifact,
        outline_hash=artifact_hash(artifact),
        approved_at="2026-08-18T00:00:00Z",
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        status="published",
    )
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    save = AsyncMock()
    monkeypatch.setattr(CourseVersion, "save", save)

    with pytest.raises(CourseConflictError):
        await CourseService.publish_version("course_version:1")

    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_version_publish_revalidates_current_approved_outline_hash(monkeypatch):
    artifact = {"chapters": [{"key": "limits"}]}
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="ready",
        outline_version_id="course_version:other",
    )
    version = CourseVersion(
        id="course_version:1",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=artifact,
        outline_hash="0" * 64,
        approved_at="2026-08-18T00:00:00Z",
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        status="published",
    )
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    save = AsyncMock()
    monkeypatch.setattr(CourseVersion, "save", save)

    with pytest.raises(CourseConflictError):
        await CourseService.publish_version("course_version:1")

    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_version_publish_uses_legal_generating_to_published_transition(
    monkeypatch,
):
    artifact = approved_outline()
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="generating",
        outline_version_id="course_version:1",
    )
    version = CourseVersion(
        id="course_version:1",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=artifact,
        outline_hash=artifact_hash(artifact),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        status="published",
    )
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    version_save = AsyncMock()

    async def publish_atomically(statement, variables=None):
        normalized = " ".join(statement.split())
        assert "BEGIN TRANSACTION" in normalized
        assert "SELECT VALUE id FROM course_generation_run" in normalized
        assert "id NOT IN $known_succeeded_run_ids" in normalized
        assert "SELECT VALUE id FROM chapter" in normalized
        assert "status = 'published'" in normalized
        assert "updated = $expected_version_updated" in normalized
        assert "UPDATE course SET" in normalized
        assert "UPDATE course_version SET" in normalized
        assert normalized.count("status = 'generating'") >= 2
        assert "COMMIT TRANSACTION" in normalized
        assert variables is not None
        assert variables["expected_version_updated"] is None
        assert [str(item) for item in variables["current_chapter_ids"]] == [
            "chapter:one"
        ]
        assert variables["known_succeeded_run_ids"] == []
        return []

    monkeypatch.setattr(CourseVersion, "save", version_save)
    monkeypatch.setattr("api.course_service.repo_query", publish_atomically)

    published = await CourseService.publish_version("course_version:1")

    assert published.status == "published"
    assert published.published_at is not None
    assert course.status == "ready"
    version_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_version_publish_requires_latest_chapter_for_every_outline_key(
    monkeypatch,
):
    artifact = approved_outline()
    artifact["chapters"].append(
        {
            "key": "derivatives",
            "title": "Derivatives",
            "purpose": "Learn derivatives.",
            "objective_keys": ["limit"],
            "anchor_ids": ["anchor:one"],
        }
    )
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="generating",
        outline_version_id="course_version:1",
    )
    version = CourseVersion(
        id="course_version:1",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=artifact,
        outline_hash=artifact_hash(artifact),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    only_limits = Chapter(
        id="chapter:limits",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        status="published",
    )
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[only_limits])
    )
    version_save = AsyncMock()
    course_save = AsyncMock()
    monkeypatch.setattr(CourseVersion, "save", version_save)
    monkeypatch.setattr(Course, "save", course_save)

    with pytest.raises(CourseConflictError, match="All chapters"):
        await CourseService.publish_version("course_version:1")

    version_save.assert_not_awaited()
    course_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_version_publish_ignores_older_immutable_chapter_versions(monkeypatch):
    artifact = approved_outline()
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="generating",
        outline_version_id="course_version:1",
    )
    version = CourseVersion(
        id="course_version:1",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=artifact,
        outline_hash=artifact_hash(artifact),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    historical = Chapter(
        id="chapter:limits-v1",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        version_no=1,
        title="Limits v1",
        status="ready",
    )
    latest = Chapter(
        id="chapter:limits-v2",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        version_no=2,
        title="Limits v2",
        status="published",
    )
    unrelated = Chapter(
        id="chapter:archived-experiment",
        course_version="course_version:1",
        chapter_no=99,
        chapter_key="not-in-approved-outline",
        version_no=1,
        title="Archived experiment",
        status="ready",
    )
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(
        CourseVersion,
        "chapters",
        AsyncMock(return_value=[historical, latest, unrelated]),
    )
    monkeypatch.setattr(CourseVersion, "save", AsyncMock())
    monkeypatch.setattr("api.course_service.repo_query", AsyncMock(return_value=[]))

    published = await CourseService.publish_version("course_version:1")

    assert published.status == "published"


@pytest.mark.asyncio
async def test_version_publish_ignores_newer_failed_partial_chapter(monkeypatch):
    artifact = approved_outline()
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="generating",
        outline_version_id="course_version:1",
    )
    version = CourseVersion(
        id="course_version:1",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=artifact,
        outline_hash=artifact_hash(artifact),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    published = Chapter(
        id="chapter:published",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        version_no=1,
        title="Limits",
        status="published",
    )
    failed_run = CourseGenerationRun(
        id="course_generation_run:failed",
        course="course:one",
        course_version="course_version:1",
        chapter="chapter:partial",
        chapter_key="limits",
        stage="chapter_content",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        reasoning_effort="max",
        status="failed",
        prompt_version="v1",
        input_hash="f" * 64,
    )
    partial = Chapter(
        id="chapter:partial",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        version_no=2,
        title="Limits partial",
        status="reviewing",
        input_hash=artifact_replay_hash(failed_run),
    )

    async def query(statement: str, variables=None):
        del variables
        if "FROM course_generation_run" in statement:
            return [failed_run.model_dump(mode="json")]
        if "BEGIN TRANSACTION" in statement:
            return []
        raise AssertionError(statement)

    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[published, partial])
    )
    monkeypatch.setattr("api.course_service.repo_query", query)
    monkeypatch.setattr("open_notebook.course.workflow_service.repo_query", query)

    result = await CourseService.publish_version("course_version:1")

    assert result.status == "published"


@pytest.mark.asyncio
async def test_version_publish_does_not_regress_concurrent_terminal_course(monkeypatch):
    artifact = approved_outline()
    stale_course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="generating",
        outline_version_id="course_version:1",
    )
    terminal_course = stale_course.model_copy(update={"status": "failed"})
    version = CourseVersion(
        id="course_version:1",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=artifact,
        outline_hash=artifact_hash(artifact),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    chapter = Chapter(
        id="chapter:limits",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        status="published",
    )
    persisted_version = version.model_copy()
    monkeypatch.setattr(
        CourseVersion,
        "get",
        AsyncMock(side_effect=[version, persisted_version]),
    )
    monkeypatch.setattr(
        Course, "get", AsyncMock(side_effect=[stale_course, terminal_course])
    )
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    version_save = AsyncMock()
    monkeypatch.setattr(CourseVersion, "save", version_save)
    course_save = AsyncMock()
    monkeypatch.setattr(Course, "save", course_save)
    monkeypatch.setattr(
        "api.course_service.repo_query",
        AsyncMock(side_effect=RuntimeError("Course publication conflict")),
    )

    with pytest.raises(CourseConflictError, match="Course is no longer generating"):
        await CourseService.publish_version("course_version:1")

    assert terminal_course.status == "failed"
    assert version.status == "generating"
    assert persisted_version.status == "generating"
    version_save.assert_not_awaited()
    course_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_transaction_uses_embedded_surreal_rollback_and_commit(
    monkeypatch,
):
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("course_publish_test", "course_publish_test")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    monkeypatch.setattr("api.course_service.repo_query", repository.repo_query)
    artifact = approved_outline()
    outline_hash = artifact_hash(artifact)
    await repository.repo_query(
        "CREATE course:one SET status = 'failed', "
        "outline_version_id = course_version:one;"
    )
    await repository.repo_query(
        "CREATE course_version:one SET course = course:one, "
        "status = 'generating', outline_hash = $outline_hash;",
        {"outline_hash": outline_hash},
    )
    await repository.repo_query(
        "CREATE chapter:one SET course_version = course_version:one, "
        "chapter_key = 'limits', status = 'published';"
    )
    stale_course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="generating",
        outline_version_id="course_version:one",
    )
    terminal_course = stale_course.model_copy(update={"status": "failed"})
    version = CourseVersion(
        id="course_version:one",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=artifact,
        outline_hash=outline_hash,
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        status="published",
    )
    monkeypatch.setattr(
        CourseVersion,
        "get",
        AsyncMock(side_effect=[version, version.model_copy()]),
    )
    monkeypatch.setattr(
        Course,
        "get",
        AsyncMock(side_effect=[stale_course, terminal_course]),
    )
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))

    with pytest.raises(CourseConflictError, match="no longer generating"):
        await CourseService.publish_version("course_version:one")

    rolled_back_version = await repository.repo_query(
        "SELECT status FROM course_version:one;"
    )
    assert rolled_back_version == [{"status": "generating"}]

    await repository.repo_query("UPDATE course:one SET status = 'generating';")
    generating_course = stale_course.model_copy()
    generating_version = version.model_copy()
    monkeypatch.setattr(
        CourseVersion, "get", AsyncMock(return_value=generating_version)
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=generating_course))

    published = await CourseService.publish_version("course_version:one")

    assert published.status == "published"
    assert await repository.repo_query("SELECT status FROM course_version:one;") == [
        {"status": "published"}
    ]
    assert await repository.repo_query("SELECT status FROM course:one;") == [
        {"status": "ready"}
    ]
    await database.close()


@pytest.mark.asyncio
async def test_publish_transaction_rejects_newly_promoted_chapter_snapshot(
    monkeypatch,
):
    import open_notebook.course.workflow_service as workflow_module
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("course_publish_race", "course_publish_race")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    artifact = approved_outline()
    outline_hash = artifact_hash(artifact)
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="generating",
        outline_version_id="course_version:one",
    )
    version = CourseVersion(
        id="course_version:one",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=artifact,
        outline_hash=outline_hash,
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    published = Chapter(
        id="chapter:published",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        version_no=1,
        title="Published limits",
        status="published",
    )
    run = CourseGenerationRun(
        id="course_generation_run:new",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:partial",
        chapter_key="limits",
        stage="chapter_content",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        status="running",
        prompt_version="v1",
        input_hash="new-claim",
        command="command:new",
    )
    partial_artifact = {"chapter_key": "limits", "sections": []}
    partial = Chapter(
        id="chapter:partial",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        version_no=2,
        title="Partial limits",
        status="reviewing",
        input_hash=artifact_replay_hash(run),
        artifact=partial_artifact,
    )
    run.output_hash = artifact_hash({"output": partial_artifact})
    await repository.repo_query(
        "CREATE course:one SET status = 'generating', "
        "outline_version_id = course_version:one;"
    )
    await repository.repo_query(
        "CREATE course_version:one SET course = course:one, "
        "status = 'generating', outline_hash = $outline_hash;",
        {"outline_hash": outline_hash},
    )
    await repository.repo_query(
        "CREATE chapter:published SET course_version = course_version:one, "
        "chapter_key = 'limits', status = 'published', input_hash = NONE;"
    )
    await repository.repo_query(
        "CREATE chapter:partial SET course_version = course_version:one, "
        "chapter_key = 'limits', status = 'reviewing', input_hash = $input_hash;",
        {"input_hash": partial.input_hash},
    )
    run_data = run._prepare_save_data()
    run_data.pop("id", None)
    await repository.repo_query(
        "CREATE course_generation_run:new CONTENT $run;", {"run": run_data}
    )
    transaction_started = False

    async def race_before_transaction(statement: str, variables=None):
        nonlocal transaction_started
        if statement.lstrip().startswith("BEGIN TRANSACTION"):
            assert not transaction_started
            transaction_started = True
            await repository.repo_query(
                "UPDATE course_generation_run:new SET status = 'succeeded';"
            )
        return await repository.repo_query(statement, variables)

    monkeypatch.setattr("api.course_service.repo_query", race_before_transaction)
    monkeypatch.setattr(workflow_module, "repo_query", repository.repo_query)
    monkeypatch.setattr(
        CourseVersion,
        "get",
        AsyncMock(side_effect=[version, version.model_copy()]),
    )
    monkeypatch.setattr(
        Course,
        "get",
        AsyncMock(side_effect=[course, course.model_copy()]),
    )
    monkeypatch.setattr(
        CourseVersion,
        "chapters",
        AsyncMock(return_value=[published, partial]),
    )

    with pytest.raises(CourseConflictError, match="no longer generating"):
        await CourseService.publish_version("course_version:one")

    assert transaction_started is True
    assert await repository.repo_query("SELECT status FROM course_version:one;") == [
        {"status": "generating"}
    ]
    assert await repository.repo_query("SELECT status FROM course:one;") == [
        {"status": "generating"}
    ]
    assert await repository.repo_query(
        "SELECT status FROM course_generation_run:new;"
    ) == [{"status": "succeeded"}]
    await database.close()


@pytest.mark.asyncio
async def test_publish_transaction_rejects_same_run_content_promotion_race(
    monkeypatch,
):
    import open_notebook.course.workflow_service as workflow_module
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("course_publish_content_race", "course_publish_content_race")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    artifact = approved_outline()
    outline_hash = artifact_hash(artifact)
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="generating",
        outline_version_id="course_version:one",
    )
    version = CourseVersion(
        id="course_version:one",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=artifact,
        outline_hash=outline_hash,
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
        updated="2026-08-18T00:00:00Z",
    )
    published = Chapter(
        id="chapter:published",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        version_no=1,
        title="Published limits",
        status="published",
    )
    partial_artifact = {"chapter_key": "limits", "sections": []}
    run = CourseGenerationRun(
        id="course_generation_run:known",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:partial",
        chapter_key="limits",
        stage="chapter_content",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        status="succeeded",
        prompt_version="v1",
        input_hash="claim",
        output_hash=artifact_hash({"output": partial_artifact}),
        command="command:known",
    )
    partial = Chapter(
        id="chapter:partial",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        version_no=2,
        title="Partial limits",
        status="reviewing",
        input_hash="wrong",
        artifact=partial_artifact,
    )
    for record in (course, version, published, partial, run):
        data = record._prepare_save_data()
        record_id = data.pop("id")
        await repository.repo_query(
            "CREATE ONLY $id CONTENT $data;",
            {"id": RecordID.parse(str(record_id)), "data": data},
        )

    changed_content = False

    async def mutate_known_chapter_before_publish(statement: str, variables=None):
        nonlocal changed_content
        if "LET $version_update" in statement and not changed_content:
            changed_content = True
            await CourseService.update_chapter(
                "course_version:one",
                "chapter:partial",
                {"input_hash": artifact_replay_hash(run)},
            )
        return await repository.repo_query(statement, variables)

    monkeypatch.setattr(
        "api.course_service.repo_query", mutate_known_chapter_before_publish
    )
    monkeypatch.setattr(workflow_module, "repo_query", repository.repo_query)

    with pytest.raises(CourseConflictError, match="no longer generating"):
        await CourseService.publish_version("course_version:one")

    assert changed_content is True
    assert await repository.repo_query("SELECT status FROM course_version:one;") == [
        {"status": "generating"}
    ]
    assert await repository.repo_query("SELECT status FROM course:one;") == [
        {"status": "generating"}
    ]
    persisted_version = await repository.repo_query(
        "SELECT updated FROM course_version:one;"
    )
    assert persisted_version[0]["updated"] != version.updated
    current = await workflow_module.CourseWorkflowService.resolve_current_chapter(
        course_id="course:one",
        version_id="course_version:one",
        chapter_key="limits",
    )
    assert str(current.id) == "chapter:partial"
    assert current.status == "reviewing"
    await database.close()


@pytest.mark.asyncio
async def test_manual_chapter_create_cas_rejects_stale_generating_version(
    monkeypatch,
):
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("manual_create_race", "manual_create_race")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    await repository.repo_query(
        "CREATE course_version:one SET course = course:one, "
        "version_no = 1, status = 'published';"
    )
    stale = CourseVersion(
        id="course_version:one",
        course="course:one",
        version_no=1,
        status="generating",
    )
    published = stale.model_copy(update={"status": "published"})
    monkeypatch.setattr(
        CourseVersion,
        "get",
        AsyncMock(side_effect=[stale, published]),
    )

    with pytest.raises(CourseImmutableError):
        await CourseService.create_chapter(
            "course_version:one",
            {
                "chapter_no": 1,
                "chapter_key": "limits",
                "title": "Late manual chapter",
            },
        )

    assert (
        await repository.repo_query(
            "SELECT id FROM chapter WHERE course_version = course_version:one;"
        )
        == []
    )
    await database.close()


@pytest.mark.asyncio
async def test_manual_chapter_update_cas_rejects_stale_generating_version(
    monkeypatch,
):
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("manual_update_race", "manual_update_race")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    await repository.repo_query(
        "CREATE course_version:one SET course = course:one, "
        "version_no = 1, status = 'published';"
    )
    await repository.repo_query(
        "CREATE chapter:one SET course_version = course_version:one, "
        "chapter_no = 1, chapter_key = 'limits', version_no = 1, "
        "title = 'Before', status = 'draft', review_status = 'pending', "
        "validation_status = 'pending';"
    )
    stale = CourseVersion(
        id="course_version:one",
        course="course:one",
        version_no=1,
        status="generating",
    )
    published = stale.model_copy(update={"status": "published"})
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        version_no=1,
        title="Before",
        status="draft",
    )
    monkeypatch.setattr(
        CourseVersion,
        "get",
        AsyncMock(side_effect=[stale, published]),
    )
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=chapter))

    with pytest.raises(CourseImmutableError):
        await CourseService.update_chapter(
            "course_version:one",
            "chapter:one",
            {"title": "Late mutation"},
        )

    assert await repository.repo_query("SELECT title FROM chapter:one;") == [
        {"title": "Before"}
    ]
    await database.close()


@pytest.mark.asyncio
async def test_chapter_promotion_transaction_rejects_published_version_in_embedded_surreal(
    monkeypatch,
):
    import open_notebook.course.workflow_service as workflow_module
    import open_notebook.database.repository as repository
    from open_notebook.course.contracts import ChapterArtifact, ChapterSection

    database = AsyncSurreal("mem://")
    await database.use("course_promotion_test", "course_promotion_test")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    monkeypatch.setattr(workflow_module, "repo_query", repository.repo_query)
    artifact = ChapterArtifact(
        chapter_key="limits",
        purpose="Learn limits.",
        objectives=["Understand limits"],
        sections=[
            ChapterSection(
                key="kept",
                title="Kept",
                markdown="Grounded.",
                anchor_ids=["anchor:one"],
            )
        ],
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        version_no=1,
        title="Limits",
        status="reviewing",
        artifact=artifact.model_dump(mode="json"),
    )
    run = CourseGenerationRun(
        id="course_generation_run:one",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="limits",
        stage="chapter_content",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        status="running",
        prompt_version="v1",
        input_hash="claim",
        command="command:one",
    )
    await repository.repo_query(
        "CREATE course_version:one SET course = course:one, status = 'published';"
    )
    await repository.repo_query(
        "CREATE chapter:one SET course_version = course_version:one, "
        "chapter_key = 'limits', input_hash = NONE;"
    )
    run_data = run._prepare_save_data()
    run_data.pop("id", None)
    await repository.repo_query(
        "CREATE course_generation_run:one CONTENT $run;", {"run": run_data}
    )
    await repository.repo_query(
        "CREATE course_note:one SET course = course:one, chapter_key = 'limits', "
        "block_key = 'removed', orphan_status = 'active';"
    )
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))

    with pytest.raises(ValueError, match="no longer active"):
        await workflow_module.CourseWorkflowService.complete_chapter_run(
            run=run,
            chapter=chapter,
            artifact=artifact,
            expected_version_updated=None,
        )

    assert await repository.repo_query(
        "SELECT status FROM course_generation_run:one;"
    ) == [{"status": "running"}]
    assert await repository.repo_query(
        "SELECT orphan_status FROM course_note:one;"
    ) == [{"orphan_status": "active"}]

    await repository.repo_query("UPDATE course_version:one SET status = 'generating';")
    await workflow_module.CourseWorkflowService.complete_chapter_run(
        run=run,
        chapter=chapter,
        artifact=artifact,
        expected_version_updated=None,
    )

    assert await repository.repo_query(
        "SELECT status FROM course_generation_run:one;"
    ) == [{"status": "succeeded"}]
    assert await repository.repo_query(
        "SELECT orphan_status FROM course_note:one;"
    ) == [{"orphan_status": "orphaned"}]
    await database.close()


def publishable_chapter_records():
    artifact = approved_outline()
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="ready",
        outline_version_id="course_version:1",
    )
    version = CourseVersion(
        id="course_version:1",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=artifact,
        outline_hash=artifact_hash(artifact),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        status="ready",
        review_status="passed",
        validation_status="passed",
    )
    return course, version, chapter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failed_gate", "expected_error"),
    [
        ("version_course_ownership", NotFoundError),
        ("chapter_version_ownership", NotFoundError),
        ("ready_status", CourseConflictError),
        ("review_passed", CourseConflictError),
        ("validation_passed", CourseConflictError),
        ("current_approved_version", CourseConflictError),
        ("approved_timestamp", CourseConflictError),
        ("server_outline_hash", CourseConflictError),
    ],
)
async def test_publish_chapter_rejects_each_failed_gate(
    monkeypatch, failed_gate, expected_error
):
    course, version, chapter = publishable_chapter_records()
    if failed_gate == "version_course_ownership":
        version.course = "course:other"
    elif failed_gate == "chapter_version_ownership":
        chapter.course_version = "course_version:other"
    elif failed_gate == "ready_status":
        chapter.status = "reviewing"
    elif failed_gate == "review_passed":
        chapter.review_status = "pending"
    elif failed_gate == "validation_passed":
        chapter.validation_status = "pending"
    elif failed_gate == "current_approved_version":
        course.outline_version_id = "course_version:other"
    elif failed_gate == "approved_timestamp":
        version.approved_at = None
    elif failed_gate == "server_outline_hash":
        version.outline_hash = "0" * 64

    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=chapter))
    save = AsyncMock()
    monkeypatch.setattr(Chapter, "save", save)

    with pytest.raises(expected_error):
        await CourseService.publish_chapter(
            "course:one", "course_version:1", "chapter:one"
        )

    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_chapter_returns_terminal_published_chapter_without_save(
    monkeypatch,
):
    course, version, chapter = publishable_chapter_records()
    chapter.status = "published"
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=chapter))
    save = AsyncMock()
    monkeypatch.setattr(Chapter, "save", save)

    published = await CourseService.publish_chapter(
        "course:one", "course_version:1", "chapter:one"
    )

    assert published is chapter
    assert published.status == "published"
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_chapter_succeeds_after_every_gate_passes(monkeypatch):
    course, version, chapter = publishable_chapter_records()
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=chapter))
    monkeypatch.setattr("api.course_service.repo_query", AsyncMock(return_value=[]))
    save = AsyncMock()
    monkeypatch.setattr(Chapter, "save", save)

    published = await CourseService.publish_chapter(
        "course:one", "course_version:1", "chapter:one"
    )

    assert published.status == "published"
    assert published.published_at is not None
    save.assert_awaited_once()


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "kind": "function_plot",
            "title": "Unsafe",
            "expressions": ["x"],
            "domain": {"x": [-1, 1]},
            "code": "alert(1)",
        },
        {
            "kind": "function_plot",
            "title": "Nested overflow",
            "expressions": ["x"],
            "objects": [{"label": "x" * 4001}],
        },
        {
            "kind": "geometry",
            "title": "Wrong discriminator",
            "objects": [],
        },
        {
            "kind": "function_plot",
            "title": "Unbounded domain key",
            "expressions": ["x"],
            "domain": {"x" * 101: [-1, 1]},
        },
    ],
)
def test_lab_router_rejects_unbounded_or_mismatched_variant_before_service(
    client, monkeypatch, payload
):
    create = AsyncMock(
        return_value=Lab(
            id="lab:one",
            course_version="course_version:one",
            lab_type="function_plot",
            payload={},
        )
    )
    monkeypatch.setattr(CourseService, "create_lab", create)

    response = client.post(
        "/api/versions/course_version:one/labs",
        json={"lab_type": "function_plot", "payload": payload},
    )

    assert response.status_code == 422
    create.assert_not_awaited()


def test_router_hides_not_found_model_and_database_details(client, monkeypatch):
    monkeypatch.setattr(
        Course,
        "get",
        AsyncMock(
            side_effect=NotFoundError(
                "Object course:missing not found - database password=do-not-leak"
            )
        ),
    )

    response = client.get("/api/courses/course:missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Course resource not found"}


def test_router_maps_repository_failure_to_sanitized_server_error(client, monkeypatch):
    monkeypatch.setattr(
        "open_notebook.course.models.repo_query",
        AsyncMock(side_effect=RuntimeError("database password=do-not-leak")),
    )

    response = client.get("/api/courses/course:one")

    assert response.status_code == 500
    assert response.json() == {"detail": "Course operation failed"}


def test_existing_notebook_repository_failure_is_not_reported_as_missing(
    client, monkeypatch
):
    monkeypatch.setattr(
        "open_notebook.domain.base.repo_query",
        AsyncMock(side_effect=RuntimeError("database password=do-not-leak")),
    )

    response = client.post(
        "/api/courses",
        json={"title": "Calculus", "notebook_id": "notebook:one"},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Course operation failed"}


def test_course_create_runs_real_router_service_and_models_with_repository_boundary(
    client, monkeypatch
):
    created_tables: list[str] = []

    async def fake_repo_create(table: str, data: dict):
        created_tables.append(table)
        return [{**data, "id": RecordID(table, "created")}]

    monkeypatch.setattr("open_notebook.domain.base.repo_create", fake_repo_create)

    response = client.post("/api/courses", json={"title": "Calculus"})

    assert response.status_code == 201
    assert response.json()["id"] == "course:created"
    assert response.json()["notebook"] == "notebook:created"
    assert created_tables == ["notebook", "course"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["progress", "note"])
async def test_stable_chapter_key_requires_owned_persisted_chapter(
    monkeypatch, operation
):
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        outline_version_id="course_version:1",
    )
    foreign_version = CourseVersion(
        id="course_version:1", course="course:other", version_no=1
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=foreign_version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[]))
    monkeypatch.setattr("api.course_service.repo_query", AsyncMock(return_value=[]))
    progress_save = AsyncMock()
    note_save = AsyncMock()
    monkeypatch.setattr(Progress, "save", progress_save)
    monkeypatch.setattr(CourseNote, "save", note_save)

    with pytest.raises(NotFoundError):
        if operation == "progress":
            await CourseService.upsert_progress(
                "course:one", {"chapter_key": "limits", "status": "in_progress"}
            )
        else:
            await CourseService.create_note(
                "course:one", {"chapter_key": "limits", "content": "note"}
            )

    progress_save.assert_not_awaited()
    note_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_attempt_keys_must_match_owned_chapter_artifact(monkeypatch):
    lab = Lab(
        id="lab:one",
        course_version="course_version:1",
        chapter="chapter:one",
        lab_type="function_plot",
        payload={},
    )
    version = CourseVersion(id="course_version:1", course="course:one", version_no=1)
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        artifact={"exercises": [{"key": "limit-core"}]},
    )
    monkeypatch.setattr(Lab, "get", AsyncMock(return_value=lab))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=chapter))
    save = AsyncMock()
    monkeypatch.setattr(Attempt, "save", save)

    with pytest.raises(NotFoundError):
        await CourseService.create_attempt(
            "lab:one",
            {
                "answers": {"value": "0"},
                "chapter_key": "limits",
                "exercise_key": "not-in-artifact",
            },
        )

    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_attempt_transition_rejects_exercise_without_owned_chapter(monkeypatch):
    attempt = Attempt(
        id="attempt:one",
        lab="lab:one",
        answers={"value": "0"},
        course="course:one",
        course_version="course_version:1",
        exercise_key="limit-core",
    )
    lab = Lab(
        id="lab:one",
        course_version="course_version:1",
        lab_type="function_plot",
        payload={},
    )
    version = CourseVersion(id="course_version:1", course="course:one", version_no=1)
    monkeypatch.setattr(Attempt, "get", AsyncMock(return_value=attempt))
    monkeypatch.setattr(Lab, "get", AsyncMock(return_value=lab))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    save = AsyncMock()
    monkeypatch.setattr(Attempt, "save", save)

    with pytest.raises(NotFoundError):
        await CourseService.transition_attempt("attempt:one", "checked")

    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_migration_24_attempt_transition_validates_lab_chapter_ownership(
    monkeypatch,
):
    attempt = Attempt(
        id="attempt:legacy",
        lab="lab:one",
        answers={"value": "0"},
    )
    lab = Lab(
        id="lab:one",
        course_version="course_version:1",
        chapter="chapter:foreign",
        lab_type="function_plot",
        payload={},
    )
    version = CourseVersion(id="course_version:1", course="course:one", version_no=1)
    foreign_chapter = Chapter(
        id="chapter:foreign",
        course_version="course_version:other",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
    )
    monkeypatch.setattr(Attempt, "get", AsyncMock(return_value=attempt))
    monkeypatch.setattr(Lab, "get", AsyncMock(return_value=lab))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=foreign_chapter))
    save = AsyncMock()
    monkeypatch.setattr(Attempt, "save", save)

    with pytest.raises(NotFoundError):
        await CourseService.transition_attempt("attempt:legacy", "checked")

    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_attempt_transition_rejects_chapter_mismatch_with_lab(monkeypatch):
    attempt = Attempt(
        id="attempt:one",
        lab="lab:one",
        answers={"value": "0"},
        course="course:one",
        course_version="course_version:1",
        chapter="chapter:one",
    )
    lab = Lab(
        id="lab:one",
        course_version="course_version:1",
        chapter="chapter:two",
        lab_type="function_plot",
        payload={},
    )
    version = CourseVersion(id="course_version:1", course="course:one", version_no=1)
    attempt_chapter = Chapter(
        id="chapter:one",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
    )
    monkeypatch.setattr(Attempt, "get", AsyncMock(return_value=attempt))
    monkeypatch.setattr(Lab, "get", AsyncMock(return_value=lab))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=attempt_chapter))
    save = AsyncMock()
    monkeypatch.setattr(Attempt, "save", save)

    with pytest.raises(NotFoundError):
        await CourseService.transition_attempt("attempt:one", "checked")

    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_lab_requires_chapter_owned_by_requested_version(monkeypatch):
    version = CourseVersion(
        id="course_version:1", course="course:one", version_no=1, status="generating"
    )
    foreign = Chapter(
        id="chapter:foreign",
        course_version="course_version:other",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
    )
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=foreign))
    save = AsyncMock()
    monkeypatch.setattr(Lab, "save", save)

    with pytest.raises(NotFoundError):
        await CourseService.create_lab(
            "course_version:1",
            {
                "chapter": "chapter:foreign",
                "lab_type": "function_plot",
                "payload": {"kind": "function_plot", "title": "Plot"},
            },
        )

    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_published_chapter_rejects_mutation_under_unpublished_version(
    monkeypatch,
):
    version = CourseVersion(
        id="course_version:1", course="course:one", version_no=1, status="generating"
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        status="published",
    )
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=chapter))
    save = AsyncMock()
    monkeypatch.setattr(Chapter, "save", save)

    with pytest.raises(CourseConflictError):
        await CourseService.update_chapter(
            "course_version:1", "chapter:one", {"title": "Mutated"}
        )

    assert chapter.title == "Limits"
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_association_rejects_source_outside_course_notebook(monkeypatch):
    course = Course(id="course:one", title="Calculus", notebook="notebook:one")
    notebook = Notebook(id="notebook:one", name="N", description="")
    source = Source(
        id="source:one",
        title="Other notebook textbook",
        asset=Asset(file_path="/private/other-notebook/textbook.pdf"),
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(Notebook, "get", AsyncMock(return_value=notebook))
    monkeypatch.setattr(Source, "get", AsyncMock(return_value=source))
    monkeypatch.setattr(Notebook, "get_sources", AsyncMock(return_value=[]))
    add_relationship = AsyncMock()
    monkeypatch.setattr(Source, "add_to_notebook", add_relationship)
    save = AsyncMock()
    monkeypatch.setattr(Course, "save", save)
    cleanup = AsyncMock()
    monkeypatch.setattr("api.course_service.repo_query", cleanup)

    with pytest.raises(CourseConflictError, match="not attached") as exc_info:
        await CourseService.associate_source("course:one", "source:one", "PRIMARY")

    assert "/private/other-notebook" not in str(exc_info.value)
    assert course.source_ids == []
    assert course.primary_source_ids == []
    add_relationship.assert_not_awaited()
    save.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("file_path", [None, "/private/course/notes.txt"])
async def test_source_association_rejects_missing_or_unsupported_original_file(
    monkeypatch,
    file_path,
):
    course = Course(id="course:one", title="Calculus", notebook="notebook:one")
    notebook = Notebook(id="notebook:one", name="N", description="")
    source = Source(
        id="source:one",
        title="Textbook",
        asset=Asset(file_path=file_path) if file_path is not None else None,
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(Notebook, "get", AsyncMock(return_value=notebook))
    monkeypatch.setattr(Source, "get", AsyncMock(return_value=source))
    monkeypatch.setattr(Notebook, "get_sources", AsyncMock(return_value=[source]))
    monkeypatch.setattr(
        EvidenceService,
        "resolve_safe_source_path",
        lambda _self, path: Path(path),
    )
    monkeypatch.setattr(
        EvidenceService,
        "_validate_file",
        staticmethod(lambda _path, _kind: None),
    )
    add_relationship = AsyncMock()
    save = AsyncMock()
    monkeypatch.setattr(Source, "add_to_notebook", add_relationship)
    monkeypatch.setattr(Course, "save", save)

    with pytest.raises(InvalidInputError) as exc_info:
        await CourseService.associate_source("course:one", "source:one", "SUPPLEMENT")

    assert "/private/course" not in str(exc_info.value)
    assert course.source_ids == []
    assert course.supplement_source_ids == []
    add_relationship.assert_not_awaited()
    save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".pdf", ".pptx"])
async def test_source_association_rejects_disguised_or_corrupt_course_file(
    monkeypatch,
    tmp_path,
    suffix,
):
    fake_document = tmp_path / f"not-a-document{suffix}"
    fake_document.write_text("plain text masquerading as a course document")
    course = Course(id="course:one", title="Calculus", notebook="notebook:one")
    notebook = Notebook(id="notebook:one", name="N", description="")
    source = Source(
        id="source:one",
        title="Textbook",
        asset=Asset(file_path=str(fake_document)),
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(Notebook, "get", AsyncMock(return_value=notebook))
    monkeypatch.setattr(Source, "get", AsyncMock(return_value=source))
    monkeypatch.setattr(Notebook, "get_sources", AsyncMock(return_value=[source]))
    monkeypatch.setattr(
        EvidenceService,
        "resolve_safe_source_path",
        lambda _self, _path: fake_document,
    )
    save = AsyncMock()
    monkeypatch.setattr(Course, "save", save)

    with pytest.raises(InvalidInputError, match="corrupt|cannot be read"):
        await CourseService.associate_source("course:one", "source:one", "PRIMARY")

    assert course.source_ids == []
    assert course.primary_source_ids == []
    save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("document_kind", ["empty-pdf", "fake-pptx"])
async def test_source_association_rejects_structurally_empty_or_fake_document(
    monkeypatch,
    tmp_path,
    document_kind,
):
    if document_kind == "empty-pdf":
        document = tmp_path / "empty.pdf"
        writer = PdfWriter()
        with document.open("wb") as stream:
            writer.write(stream)
    else:
        document = tmp_path / "fake.pptx"
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("[Content_Types].xml", "not xml")
            archive.writestr("ppt/slides/slide1.xml", "not xml")

    course = Course(id="course:one", title="Calculus", notebook="notebook:one")
    notebook = Notebook(id="notebook:one", name="N", description="")
    source = Source(
        id="source:one",
        title="Textbook",
        asset=Asset(file_path=str(document)),
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(Notebook, "get", AsyncMock(return_value=notebook))
    monkeypatch.setattr(Source, "get", AsyncMock(return_value=source))
    monkeypatch.setattr(Notebook, "get_sources", AsyncMock(return_value=[source]))
    monkeypatch.setattr(
        EvidenceService,
        "resolve_safe_source_path",
        lambda _self, _path: document,
    )
    save = AsyncMock()
    monkeypatch.setattr(Course, "save", save)

    with pytest.raises(InvalidInputError, match="no readable|corrupt"):
        await CourseService.associate_source("course:one", "source:one", "PRIMARY")

    assert str(tmp_path) not in str(save.call_args_list)
    assert course.source_ids == []
    assert course.primary_source_ids == []
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_association_rolls_back_roles_when_course_save_fails(monkeypatch):
    course = Course(id="course:one", title="Calculus", notebook="notebook:one")
    notebook = Notebook(id="notebook:one", name="N", description="")
    source = Source(
        id="source:one",
        title="Textbook",
        asset=Asset(file_path="/safe/textbook.pptx"),
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(Notebook, "get", AsyncMock(return_value=notebook))
    monkeypatch.setattr(Source, "get", AsyncMock(return_value=source))
    monkeypatch.setattr(Notebook, "get_sources", AsyncMock(return_value=[source]))
    monkeypatch.setattr(
        EvidenceService,
        "resolve_safe_source_path",
        lambda _self, path: Path(path),
    )
    monkeypatch.setattr(
        EvidenceService,
        "_validate_file",
        staticmethod(lambda _path, _kind: None),
    )
    add_relationship = AsyncMock()
    monkeypatch.setattr(Source, "add_to_notebook", add_relationship)
    monkeypatch.setattr(Course, "save", AsyncMock(side_effect=RuntimeError("db")))

    with pytest.raises(RuntimeError, match="db"):
        await CourseService.associate_source("course:one", "source:one", "PRIMARY")

    assert course.source_ids == []
    assert course.primary_source_ids == []
    add_relationship.assert_not_awaited()
