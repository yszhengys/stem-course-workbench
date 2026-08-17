import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from surrealdb import RecordID

from api.course_service import CourseConflictError, CourseService
from open_notebook.course.models import (
    Attempt,
    Chapter,
    Course,
    CourseNote,
    CourseVersion,
    Lab,
    Progress,
)
from open_notebook.domain.notebook import Notebook, Source
from open_notebook.exceptions import NotFoundError


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
async def test_version_publish_uses_legal_generating_to_published_transition(monkeypatch):
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
        status="generating",
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

    published = await CourseService.publish_version("course_version:1")

    assert published.status == "published"
    assert published.published_at is not None
    save.assert_awaited_once()


def publishable_chapter_records():
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
        status="generating",
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
    monkeypatch.setattr(
        CourseVersion, "get", AsyncMock(return_value=foreign_version)
    )
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
    version = CourseVersion(
        id="course_version:1", course="course:one", version_no=1
    )
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
    version = CourseVersion(
        id="course_version:1", course="course:one", version_no=1
    )
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
    version = CourseVersion(
        id="course_version:1", course="course:one", version_no=1
    )
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
    version = CourseVersion(
        id="course_version:1", course="course:one", version_no=1
    )
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
async def test_published_chapter_rejects_mutation_under_unpublished_version(monkeypatch):
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
async def test_source_association_compensates_new_notebook_relationship(monkeypatch):
    course = Course(
        id="course:one", title="Calculus", notebook="notebook:one"
    )
    notebook = Notebook(id="notebook:one", name="N", description="")
    source = Source(id="source:one", title="Textbook")
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(Notebook, "get", AsyncMock(return_value=notebook))
    monkeypatch.setattr(Source, "get", AsyncMock(return_value=source))
    monkeypatch.setattr(Notebook, "get_sources", AsyncMock(return_value=[]))
    add_relationship = AsyncMock()
    monkeypatch.setattr(Source, "add_to_notebook", add_relationship)
    monkeypatch.setattr(Course, "save", AsyncMock(side_effect=RuntimeError("db")))
    cleanup = AsyncMock()
    monkeypatch.setattr("api.course_service.repo_query", cleanup)

    with pytest.raises(RuntimeError):
        await CourseService.associate_source(
            "course:one", "source:one", "PRIMARY"
        )

    add_relationship.assert_awaited_once_with("notebook:one")
    cleanup.assert_awaited_once()
    cleanup_call = cleanup.await_args
    assert cleanup_call is not None
    assert "DELETE reference" in cleanup_call.args[0]
