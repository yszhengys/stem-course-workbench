from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from surrealdb import RecordID

from api.course_service import (
    CourseApprovalError,
    CourseConflictError,
    CourseService,
)
from open_notebook.course import state_machine as sm
from open_notebook.course.contracts import (
    FunctionPlotLabSpec,
    GenerationResult,
    ModelSelection,
)
from open_notebook.course.models import (
    DEFAULT_MODEL_POLICY,
    Chapter,
    Course,
    CourseVersion,
)
from open_notebook.database.async_migrate import AsyncMigrationManager
from open_notebook.domain.notebook import Notebook, Source
from open_notebook.exceptions import NotFoundError


def test_migration_25_is_additive_registered_and_preserves_migration_24_tables():
    manager = AsyncMigrationManager()
    migration = Path("open_notebook/database/migrations/25.surrealql").read_text()
    down = Path("open_notebook/database/migrations/25_down.surrealql").read_text()

    assert len(manager.up_migrations) == 25
    assert len(manager.down_migrations) == 25
    assert "course_anchor_identity_unique" in manager.up_migrations[-1].sql
    assert "course_evidence_anchor" in manager.down_migrations[-1].sql
    assert "course_outline_version" not in migration
    assert "course_chapter_version" not in migration
    for table in (
        "course_evidence_anchor",
        "course_generation_run",
        "course_validation_finding",
    ):
        assert f"DEFINE TABLE IF NOT EXISTS {table}" in migration
    for migration_24_table in (
        "course",
        "course_version",
        "chapter",
        "evidence",
        "lab",
        "attempt",
        "progress",
        "course_note",
    ):
        assert f"REMOVE TABLE IF EXISTS {migration_24_table};" not in down


def test_public_contracts_forbid_extra_input_and_lock_model_defaults():
    assert DEFAULT_MODEL_POLICY["outline"] == ModelSelection(
        adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
    )
    assert DEFAULT_MODEL_POLICY["review"] == ModelSelection(
        adapter="codex_cli", model="gpt-5.6-luna", reasoning_effort="max"
    )
    assert ModelSelection(adapter="open_notebook", model="deepseek-chat")
    with pytest.raises(ValidationError):
        ModelSelection(adapter="deepseek", model="deepseek-chat")
    with pytest.raises(ValidationError):
        FunctionPlotLabSpec(
            kind="function_plot",
            title="unsafe",
            expressions=["__import__('os')"],
            domain={"x": (-1.0, 1.0)},
            code="alert(1)",  # type: ignore[call-arg]
        )
    assert GenerationResult[int](success=True, stage="outline", output=1).output == 1


@pytest.mark.parametrize(
    ("machine", "current", "target"),
    [
        ("course", "draft", "indexing"),
        ("course", "indexing", "outline_ready"),
        ("course", "outline_ready", "outline_approved"),
        ("course", "outline_approved", "generating"),
        ("course", "generating", "ready"),
        ("chapter", "draft", "generating"),
        ("chapter", "generating", "reviewing"),
        ("chapter", "reviewing", "blocked"),
        ("chapter", "blocked", "generating"),
        ("chapter", "reviewing", "ready"),
        ("chapter", "ready", "published"),
        ("run", "queued", "running"),
        ("run", "running", "succeeded"),
    ],
)
def test_course_workflow_declares_only_explicit_lifecycle_steps(
    machine: str, current: str, target: str
):
    assert sm.transition(machine, current, target) == target


def test_published_chapter_and_completed_run_are_terminal():
    assert sm.is_terminal("chapter", "published")
    assert sm.is_terminal("run", "succeeded")
    with pytest.raises(Exception):
        sm.transition("chapter", "published", "generating")


@pytest.mark.asyncio
async def test_typed_id_confusion_is_rejected_before_lookup(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(Course, "get", lookup)

    with pytest.raises(NotFoundError):
        await CourseService.get_course("course_note:not-a-course")

    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_outline_approval_requires_current_version_state_and_exact_phrase(monkeypatch):
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="outline_ready",
        outline_version_id="course_version:2",
    )
    version = CourseVersion(
        id="course_version:2",
        course="course:one",
        version_no=2,
        outline_artifact={"chapters": [{"key": "limits"}]},
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Course, "save", AsyncMock())
    monkeypatch.setattr(CourseVersion, "save", AsyncMock())

    with pytest.raises(CourseApprovalError):
        await CourseService.approve_outline("course:one", "course_version:2", "确认")
    approved = await CourseService.approve_outline(
        "course:one", "course_version:2", " 确认大纲\n"
    )

    assert approved.confirmation == "确认大纲"
    assert approved.approved_at is not None
    assert course.status == "outline_approved"


@pytest.mark.asyncio
async def test_chapter_patch_validates_all_transitions_before_one_save(monkeypatch):
    version = CourseVersion(id="course_version:1", course="course:one", version_no=1)
    chapter = Chapter(
        id="chapter:1",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        title="Before",
    )
    save = AsyncMock()
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=chapter))
    monkeypatch.setattr(Chapter, "save", save)

    with pytest.raises(Exception):
        await CourseService.update_chapter(
            "course_version:1",
            "chapter:1",
            {"title": "After", "review_status": "passed", "validation_status": "nonsense"},
        )

    assert chapter.title == "Before"
    assert chapter.review_status == "pending"
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_published_version_rejects_child_mutation_and_parent_mismatch(monkeypatch):
    published = CourseVersion(
        id="course_version:1", course="course:one", version_no=1, status="published"
    )
    foreign_chapter = Chapter(
        id="chapter:other",
        course_version="course_version:other",
        chapter_no=1,
        chapter_key="foreign",
        title="Foreign",
    )
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=published))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=foreign_chapter))
    monkeypatch.setattr(Chapter, "save", AsyncMock())

    with pytest.raises(CourseConflictError):
        await CourseService.create_chapter(
            "course_version:1", {"chapter_no": 1, "title": "No mutation"}
        )
    with pytest.raises(NotFoundError):
        await CourseService.update_chapter(
            "course_version:1", "chapter:other", {"title": "No mutation"}
        )


@pytest.mark.asyncio
async def test_source_association_keeps_exactly_one_role(monkeypatch):
    course = Course(
        id="course:one", title="Calculus", notebook="notebook:one"
    )
    source = Source(id="source:one", title="Textbook")
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(
        Notebook,
        "get",
        AsyncMock(return_value=Notebook(id="notebook:one", name="N", description="")),
    )
    monkeypatch.setattr(Source, "get", AsyncMock(return_value=source))
    monkeypatch.setattr(Notebook, "get_sources", AsyncMock(return_value=[]))
    monkeypatch.setattr(Source, "add_to_notebook", AsyncMock())
    save = AsyncMock()
    monkeypatch.setattr(Course, "save", save)

    associated = await CourseService.associate_source(
        "course:one", "source:one", "PRIMARY"
    )

    assert associated.source_ids == ["source:one"]
    assert associated.primary_source_ids == ["source:one"]
    assert associated.supplement_source_ids == []
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_course_creation_compensates_new_notebook_on_course_failure(monkeypatch):
    async def save_notebook(notebook):
        notebook.id = "notebook:new"

    monkeypatch.setattr(Notebook, "save", save_notebook)
    delete_notebook = AsyncMock()
    monkeypatch.setattr(Notebook, "delete", delete_notebook)
    monkeypatch.setattr(Course, "save", AsyncMock(side_effect=RuntimeError("db")))

    with pytest.raises(RuntimeError):
        await CourseService.create_course(title="Calculus")

    delete_notebook.assert_awaited_once()


@pytest.mark.asyncio
async def test_chapter_regeneration_uses_next_unique_version(monkeypatch):
    version = CourseVersion(id="course_version:1", course="course:one", version_no=1)
    prior = Chapter(
        id="chapter:1",
        course_version="course_version:1",
        chapter_no=1,
        chapter_key="limits",
        version_no=2,
        title="Limits",
        status="ready",
    )
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[prior]))
    monkeypatch.setattr(Chapter, "save", AsyncMock())

    regenerated = await CourseService.create_chapter(
        "course_version:1",
        {"chapter_no": 1, "chapter_key": "limits", "title": "Limits v3"},
    )

    assert regenerated.version_no == 3
    assert prior.title == "Limits"


def test_course_record_fields_serialize_to_record_ids_and_return_as_strings():
    course = Course(
        title="Calculus",
        notebook=RecordID("notebook", "one"),
        source_ids=[RecordID("source", "one")],
        primary_source_ids=[RecordID("source", "one")],
    )

    assert course.notebook == "notebook:one"
    assert course.source_ids == ["source:one"]
    stored = course._prepare_save_data()
    assert isinstance(stored["notebook"], RecordID)
    assert isinstance(stored["source_ids"][0], RecordID)


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


def test_course_router_uses_service_201_and_has_no_generic_status_bypass(client, monkeypatch):
    created = Course(
        id="course:one", title="Calculus", notebook="notebook:one"
    )
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(CourseService, "create_course", create)

    response = client.post("/api/courses", json={"title": "Calculus"})
    bypass = client.post(
        "/api/courses/course:one/status", json={"status": "outline_approved"}
    )

    assert response.status_code == 201
    assert response.json()["notebook"] == "notebook:one"
    assert bypass.status_code == 404
    create.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (CourseApprovalError("Type exactly: 确认大纲"), 422),
        (CourseConflictError("Outline version is stale"), 409),
    ],
)
def test_outline_approval_maps_input_and_state_failures(
    client, monkeypatch, error, expected_status
):
    monkeypatch.setattr(CourseService, "approve_outline", AsyncMock(side_effect=error))
    response = client.post(
        "/api/courses/course:one/outline/approve",
        json={"version_id": "course_version:1", "confirmation": "确认"},
    )

    assert response.status_code == expected_status
