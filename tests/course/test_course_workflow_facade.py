"""High-level Course workflow facades resolve record IDs on the server."""

import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.course_service import CourseConflictError, CourseService
from api.models import CourseNoteCreate, ProgressUpdate
from open_notebook.course.contracts import ModelSelection
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
from open_notebook.exceptions import InvalidInputError, NotFoundError


def _current_course() -> Course:
    return Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        outline_version_id="course_version:current",
        status="generating",
    )


def _current_version() -> CourseVersion:
    outline = {
        "title": "Calculus",
        "chapters": [
            {
                "key": "limits",
                "title": "Limits",
                "purpose": "Learn limits.",
                "objective_keys": ["limit"],
                "anchor_ids": ["anchor:one"],
                "lab_keys": ["limit-plot"],
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
    return CourseVersion(
        id="course_version:current",
        course="course:one",
        version_no=2,
        status="generating",
        outline_artifact=outline,
        outline_hash=hashlib.sha256(
            json.dumps(
                outline,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )


def _chapter(*, record_id: str, version_no: int) -> Chapter:
    return Chapter(
        id=record_id,
        course_version="course_version:current",
        chapter_no=1,
        chapter_key="limits",
        version_no=version_no,
        title="Limits",
        status="ready",
        review_status="passed",
        validation_status="passed",
        artifact={
            "sections": [{"key": "intro"}],
            "labs": [{"key": "limit-plot"}],
            "exercises": [{"key": "limit-core"}],
        },
    )


def _failed_partial_chapter() -> tuple[Chapter, CourseGenerationRun]:
    run = CourseGenerationRun(
        id="course_generation_run:failed-partial",
        course="course:one",
        course_version="course_version:current",
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
    chapter = _chapter(record_id="chapter:partial", version_no=2)
    chapter.status = "reviewing"
    chapter.input_hash = artifact_replay_hash(run)
    return chapter, run


def _run_output_hash(chapter: Chapter) -> str:
    return hashlib.sha256(
        json.dumps(
            {"output": chapter.artifact or {}},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@pytest.mark.asyncio
async def test_legacy_succeeded_unbound_run_promotes_unique_matching_chapter(
    monkeypatch,
):
    from api.course_command_service import CourseCommandService

    course = _current_course()
    version = _current_version()
    previous = _chapter(record_id="chapter:published", version_no=1)
    generated = _chapter(record_id="chapter:generated", version_no=2)
    run = CourseGenerationRun(
        id="course_generation_run:legacy-success",
        course="course:one",
        course_version="course_version:current",
        chapter_key="limits",
        stage="chapter_content",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        reasoning_effort="max",
        status="succeeded",
        prompt_version="v1",
        input_hash="a" * 64,
    )
    generated.input_hash = artifact_replay_hash(run)
    run.output_hash = _run_output_hash(generated)

    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[previous, generated])
    )
    monkeypatch.setattr(
        "open_notebook.course.workflow_service.repo_query",
        AsyncMock(return_value=[run.model_dump(mode="json")]),
    )

    current = await CourseCommandService.current_chapter("course:one", "limits")

    assert current.id == "chapter:generated"


@pytest.mark.asyncio
async def test_legacy_succeeded_unbound_run_ambiguity_fails_closed(monkeypatch):
    from api.course_command_service import CourseCommandService

    course = _current_course()
    version = _current_version()
    previous = _chapter(record_id="chapter:published", version_no=1)
    first = _chapter(record_id="chapter:generated-one", version_no=2)
    duplicate = _chapter(record_id="chapter:generated-two", version_no=3)
    run = CourseGenerationRun(
        id="course_generation_run:legacy-ambiguous",
        course="course:one",
        course_version="course_version:current",
        chapter_key="limits",
        stage="chapter_content",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        reasoning_effort="max",
        status="succeeded",
        prompt_version="v1",
        input_hash="b" * 64,
    )
    first.input_hash = artifact_replay_hash(run)
    duplicate.input_hash = first.input_hash
    run.output_hash = _run_output_hash(first)

    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(
        CourseVersion,
        "chapters",
        AsyncMock(return_value=[previous, first, duplicate]),
    )
    monkeypatch.setattr(
        "open_notebook.course.workflow_service.repo_query",
        AsyncMock(return_value=[run.model_dump(mode="json")]),
    )

    current = await CourseCommandService.current_chapter("course:one", "limits")

    assert current.id == "chapter:published"


@pytest.mark.asyncio
async def test_failed_partial_chapter_does_not_replace_current_facades(monkeypatch):
    from api.course_command_service import CourseCommandService

    course = _current_course()
    version = _current_version()
    current = _chapter(record_id="chapter:published", version_no=1)
    current.status = "published"
    partial, failed_run = _failed_partial_chapter()
    lab = Lab(
        id="lab:published",
        course_version="course_version:current",
        chapter="chapter:published",
        lab_type="function_plot",
        payload={"key": "limit-plot", "kind": "function_plot"},
    )

    async def query(statement: str, variables=None):
        del variables
        if "FROM course_generation_run" in statement:
            return [failed_run.model_dump(mode="json")]
        if "FROM progress" in statement:
            return []
        raise AssertionError(statement)

    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[current, partial])
    )
    monkeypatch.setattr(CourseVersion, "labs", AsyncMock(return_value=[lab]))
    monkeypatch.setattr("api.course_service.repo_query", query)
    monkeypatch.setattr("open_notebook.course.workflow_service.repo_query", query)
    monkeypatch.setattr(Attempt, "save", AsyncMock())
    monkeypatch.setattr(Progress, "save", AsyncMock())
    monkeypatch.setattr(CourseNote, "save", AsyncMock())
    monkeypatch.setattr(
        CourseService, "publish_chapter", AsyncMock(return_value=current)
    )

    fetched = await CourseCommandService.current_chapter("course:one", "limits")
    published = await CourseService.publish_current_chapter("course:one", "limits")
    labs = await CourseService.list_chapter_labs("course:one", "limits")
    attempt = await CourseService.create_chapter_attempt(
        "course:one",
        "limits",
        "limit-plot",
        {"answers": {"value": "1"}, "exercise_key": "limit-core"},
    )
    progress = await CourseService.upsert_progress(
        "course:one",
        {"chapter_key": "limits", "block_key": "intro", "status": "in_progress"},
    )
    note = await CourseService.create_note(
        "course:one",
        {"chapter_key": "limits", "block_key": "intro", "content": "Keep."},
    )

    assert fetched.id == "chapter:published"
    assert published.id == "chapter:published"
    assert labs[0]["id"] == "lab:published"
    assert attempt.chapter == "chapter:published"
    assert progress.chapter == "chapter:published"
    assert note.chapter == "chapter:published"


@pytest.mark.asyncio
async def test_review_submission_targets_last_successfully_promoted_chapter(monkeypatch):
    from api.course_command_service import CourseCommandService, CourseJobSubmission

    course = _current_course()
    version = _current_version()
    current = _chapter(record_id="chapter:published", version_no=1)
    current.status = "published"
    partial, failed_run = _failed_partial_chapter()
    service = CourseCommandService()

    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[current, partial])
    )
    monkeypatch.setattr(
        "open_notebook.course.workflow_service.repo_query",
        AsyncMock(return_value=[failed_run.model_dump(mode="json")]),
    )
    monkeypatch.setattr(
        service,
        "_grounded",
        AsyncMock(return_value=(course, {"source:one": "a" * 64}, [])),
    )

    async def submit_stage(**kwargs):
        return CourseJobSubmission(
            command_id=str(kwargs["chapter_id"]),
            run_id="course_generation_run:review",
            status="queued",
        )

    monkeypatch.setattr(service, "submit_stage", submit_stage)

    submission = await service.submit_review(
        course_id="course:one",
        chapter_key="limits",
        anchor_ids=["anchor:one"],
        prompt_version="v1",
        model=ModelSelection(
            adapter="codex_cli",
            model="gpt-5.6-luna",
            reasoning_effort="max",
        ),
    )

    assert submission.command_id == "chapter:published"


@pytest.mark.asyncio
async def test_publish_current_chapter_resolves_current_version_and_latest_chapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _current_course()
    version = _current_version()
    older = _chapter(record_id="chapter:older", version_no=1)
    latest = _chapter(record_id="chapter:latest", version_no=2)
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[latest, older])
    )
    publish = AsyncMock(return_value=latest)
    monkeypatch.setattr(CourseService, "publish_chapter", publish)

    result = await CourseService.publish_current_chapter("course:one", "limits")

    assert result is latest
    publish.assert_awaited_once_with(
        "course:one", "course_version:current", "chapter:latest"
    )


def test_publish_current_chapter_route_never_accepts_record_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = _chapter(record_id="chapter:latest", version_no=2)
    publish = AsyncMock(return_value=latest)
    monkeypatch.setattr(CourseService, "publish_current_chapter", publish)

    from api.main import app

    response = TestClient(app).post(
        "/api/courses/course:one/chapters/limits/publish"
    )

    assert response.status_code == 200
    assert response.json()["id"] == "chapter:latest"
    publish.assert_awaited_once_with("course:one", "limits")


@pytest.mark.asyncio
async def test_reattach_note_resolves_current_chapter_and_real_stable_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _current_course()
    version = _current_version()
    chapter = _chapter(record_id="chapter:latest", version_no=2)
    note = CourseNote(
        id="course_note:one",
        course="course:one",
        chapter="chapter:older",
        chapter_key="limits",
        block_key="removed",
        orphan_status="orphaned",
        content="Keep this note",
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(CourseNote, "get", AsyncMock(return_value=note))
    save = AsyncMock()
    monkeypatch.setattr(CourseNote, "save", save)

    result = await CourseService.reattach_note(
        "course:one",
        "course_note:one",
        chapter_key="limits",
        block_key="intro",
    )

    assert result is note
    assert note.chapter == "chapter:latest"
    assert note.chapter_key == "limits"
    assert note.block_key == "intro"
    assert note.orphan_status == "active"
    save.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign_note", "missing_block"])
async def test_reattach_note_fails_closed_on_ownership_or_unknown_block(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    course = _current_course()
    version = _current_version()
    chapter = _chapter(record_id="chapter:latest", version_no=2)
    note = CourseNote(
        id="course_note:one",
        course="course:other" if failure == "foreign_note" else "course:one",
        chapter_key="limits",
        block_key="removed",
        orphan_status="orphaned",
        content="Keep this note",
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(CourseNote, "get", AsyncMock(return_value=note))
    save = AsyncMock()
    monkeypatch.setattr(CourseNote, "save", save)

    with pytest.raises(NotFoundError):
        await CourseService.reattach_note(
            "course:one",
            "course_note:one",
            chapter_key="limits",
            block_key="unknown" if failure == "missing_block" else "intro",
        )

    save.assert_not_awaited()


def test_note_reattach_route_is_strict_and_never_accepts_chapter_record_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    note = CourseNote(
        id="course_note:one",
        course="course:one",
        chapter="chapter:latest",
        chapter_key="limits",
        block_key="intro",
        content="Keep this note",
    )
    reattach = AsyncMock(return_value=note)
    monkeypatch.setattr(CourseService, "reattach_note", reattach)

    from api.main import app

    client = TestClient(app)
    accepted = client.patch(
        "/api/courses/course:one/notes/course_note:one",
        json={"chapter_key": "limits", "block_key": "intro"},
    )
    rejected = client.patch(
        "/api/courses/course:one/notes/course_note:one",
        json={
            "chapter_key": "limits",
            "block_key": "intro",
            "chapter": "chapter:latest",
        },
    )

    assert accepted.status_code == 200
    assert accepted.json()["orphan_status"] == "active"
    assert rejected.status_code == 422
    reattach.assert_awaited_once_with(
        "course:one",
        "course_note:one",
        chapter_key="limits",
        block_key="intro",
    )


def _lab(
    *, record_id: str, chapter_id: str, lab_key: str, version_id: str = "course_version:current"
) -> Lab:
    return Lab(
        id=record_id,
        course_version=version_id,
        chapter=chapter_id,
        lab_type="function_plot",
        payload={
            "kind": "function_plot",
            "key": lab_key,
            "title": "Explore limits",
            "anchor_ids": ["anchor:one"],
            "expressions": ["x"],
            "domain": {"x": [-1.0, 1.0]},
            "controls": [],
            "objects": [],
        },
    )


@pytest.mark.asyncio
async def test_chapter_labs_map_stable_keys_to_current_persistent_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _current_course()
    version = _current_version()
    chapter = _chapter(record_id="chapter:latest", version_no=2)
    current = _lab(
        record_id="lab:current", chapter_id="chapter:latest", lab_key="limit-plot"
    )
    older = _lab(
        record_id="lab:older", chapter_id="chapter:older", lab_key="limit-plot"
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(
        CourseVersion, "labs", AsyncMock(return_value=[older, current])
    )

    result = await CourseService.list_chapter_labs("course:one", "limits")

    assert result == [
        {
            "id": "lab:current",
            "lab_key": "limit-plot",
            "lab_type": "function_plot",
            "spec": current.payload,
        }
    ]


@pytest.mark.asyncio
async def test_chapter_labs_reject_duplicate_or_missing_persistent_stable_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _current_course()
    version = _current_version()
    chapter = _chapter(record_id="chapter:latest", version_no=2)
    first = _lab(
        record_id="lab:first", chapter_id="chapter:latest", lab_key="limit-plot"
    )
    duplicate = _lab(
        record_id="lab:duplicate",
        chapter_id="chapter:latest",
        lab_key="limit-plot",
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(
        CourseVersion, "labs", AsyncMock(return_value=[first, duplicate])
    )

    with pytest.raises(CourseConflictError, match="stable key"):
        await CourseService.list_chapter_labs("course:one", "limits")


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["missing", "unknown"])
async def test_chapter_labs_fail_closed_when_artifact_and_records_diverge(
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    course = _current_course()
    version = _current_version()
    chapter = _chapter(record_id="chapter:latest", version_no=2)
    labs = (
        []
        if mismatch == "missing"
        else [
            _lab(
                record_id="lab:unknown",
                chapter_id="chapter:latest",
                lab_key="not-in-artifact",
            )
        ]
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(CourseVersion, "labs", AsyncMock(return_value=labs))

    with pytest.raises(CourseConflictError, match="stable keys"):
        await CourseService.list_chapter_labs("course:one", "limits")


@pytest.mark.asyncio
async def test_current_chapter_facades_reject_tampered_approved_outline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _current_course()
    version = _current_version()
    version.outline_hash = "0" * 64
    chapter = _chapter(record_id="chapter:latest", version_no=2)
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(CourseVersion, "labs", AsyncMock(return_value=[]))

    with pytest.raises(CourseConflictError, match="approved"):
        await CourseService.list_chapter_labs("course:one", "limits")


@pytest.mark.asyncio
async def test_current_chapter_facades_reject_unparseable_approved_outline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _current_course()
    version = _current_version()
    invalid_outline = {"chapters": [{"key": "limits"}]}
    version.outline_artifact = invalid_outline
    version.outline_hash = hashlib.sha256(
        json.dumps(
            invalid_outline,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[]))

    with pytest.raises(CourseConflictError, match="approved"):
        await CourseService.list_chapter_labs("course:one", "limits")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["labs", "progress", "note", "attempt"])
async def test_current_chapter_facades_require_exact_outline_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    course = _current_course()
    version = _current_version()
    version.confirmation = None
    chapter = _chapter(record_id="chapter:latest", version_no=2)
    lab = _lab(
        record_id="lab:current",
        chapter_id="chapter:latest",
        lab_key="limit-plot",
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(CourseVersion, "labs", AsyncMock(return_value=[lab]))
    monkeypatch.setattr(
        "api.course_service.repo_query", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(Progress, "save", AsyncMock())
    monkeypatch.setattr(CourseNote, "save", AsyncMock())
    monkeypatch.setattr(Attempt, "save", AsyncMock())

    with pytest.raises(CourseConflictError, match="approved"):
        if operation == "labs":
            await CourseService.list_chapter_labs("course:one", "limits")
        elif operation == "progress":
            await CourseService.upsert_progress(
                "course:one",
                {"chapter_key": "limits", "status": "in_progress"},
            )
        elif operation == "note":
            await CourseService.create_note(
                "course:one", {"chapter_key": "limits", "content": "note"}
            )
        else:
            await CourseService.create_chapter_attempt(
                "course:one",
                "limits",
                "limit-plot",
                {"answers": {"answer": "0"}},
            )


@pytest.mark.asyncio
async def test_publish_rechecks_unresolved_findings_as_final_defense(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _current_course()
    version = _current_version()
    chapter = _chapter(record_id="chapter:latest", version_no=2)
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=chapter))
    monkeypatch.setattr(
        "api.course_service.repo_query",
        AsyncMock(
            return_value=[
                {
                    "finding": {
                        "kind": "review",
                        "severity": "high",
                        "item_key": "definition",
                        "anchor_ids": ["anchor:one"],
                        "status": "open",
                        "message": "The definition is unsafe to publish.",
                    }
                }
            ]
        ),
    )
    save = AsyncMock()
    monkeypatch.setattr(Chapter, "save", save)

    with pytest.raises(CourseConflictError, match="finding"):
        await CourseService.publish_chapter(
            "course:one", "course_version:current", "chapter:latest"
        )

    save.assert_not_awaited()


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            ProgressUpdate,
            {
                "chapter_key": "limits",
                "status": "in_progress",
                "chapter": "chapter:old",
            },
        ),
        (
            ProgressUpdate,
            {
                "chapter_key": "limits",
                "status": "in_progress",
                "unexpected": True,
            },
        ),
        (
            CourseNoteCreate,
            {
                "chapter_key": "limits",
                "content": "note",
                "chapter": "chapter:old",
            },
        ),
        (
            CourseNoteCreate,
            {
                "chapter_key": "limits",
                "content": "note",
                "unexpected": True,
            },
        ),
    ],
)
def test_progress_and_note_requests_forbid_record_ids_and_extra_fields(
    model: type[ProgressUpdate] | type[CourseNoteCreate],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["progress", "note"])
async def test_progress_and_note_services_reject_client_chapter_record_id(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    course = _current_course()
    version = _current_version()
    old = _chapter(record_id="chapter:old", version_no=1)
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=old))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[old]))
    progress_save = AsyncMock()
    note_save = AsyncMock()
    monkeypatch.setattr(Progress, "save", progress_save)
    monkeypatch.setattr(CourseNote, "save", note_save)
    monkeypatch.setattr(
        "api.course_service.repo_query", AsyncMock(return_value=[])
    )

    with pytest.raises(InvalidInputError, match="chapter record"):
        if operation == "progress":
            await CourseService.upsert_progress(
                "course:one",
                {
                    "chapter": "chapter:old",
                    "chapter_key": "limits",
                    "status": "in_progress",
                },
            )
        else:
            await CourseService.create_note(
                "course:one",
                {
                    "chapter": "chapter:old",
                    "chapter_key": "limits",
                    "content": "note",
                },
            )

    progress_save.assert_not_awaited()
    note_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_chapter_attempt_resolves_lab_key_and_saves_v1_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _current_course()
    version = _current_version()
    chapter = _chapter(record_id="chapter:latest", version_no=2)
    lab = _lab(
        record_id="lab:current", chapter_id="chapter:latest", lab_key="limit-plot"
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(CourseVersion, "labs", AsyncMock(return_value=[lab]))
    save = AsyncMock()
    monkeypatch.setattr(Attempt, "save", save)

    attempt = await CourseService.create_chapter_attempt(
        "course:one",
        "limits",
        "limit-plot",
        {
            "answers": {"answer": "0"},
            "exercise_key": "limit-core",
            "answer": "zero",
            "hints_used": 2,
            "answer_revealed": True,
            "transfer_completed": False,
        },
    )

    assert attempt.lab == "lab:current"
    assert attempt.course == "course:one"
    assert attempt.course_version == "course_version:current"
    assert attempt.chapter == "chapter:latest"
    assert attempt.chapter_key == "limits"
    assert attempt.exercise_key == "limit-core"
    assert attempt.answer == "zero"
    assert attempt.hints_used == 2
    assert attempt.answer_revealed is True
    assert attempt.transfer_completed is False
    assert attempt.orphan_status == "active"
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_chapter_attempt_rejects_unknown_stable_lab_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _current_course()
    version = _current_version()
    chapter = _chapter(record_id="chapter:latest", version_no=2)
    lab = _lab(
        record_id="lab:current", chapter_id="chapter:latest", lab_key="limit-plot"
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(CourseVersion, "labs", AsyncMock(return_value=[lab]))
    save = AsyncMock()
    monkeypatch.setattr(Attempt, "save", save)

    with pytest.raises(NotFoundError):
        await CourseService.create_chapter_attempt(
            "course:one",
            "limits",
            "missing-lab",
            {"answers": {"answer": "0"}},
        )

    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_attempt_history_preserves_old_versions_and_explicit_lab_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = _current_course()
    current_version = _current_version()
    old_version = CourseVersion(
        id="course_version:old",
        course="course:one",
        version_no=1,
        status="published",
        outline_artifact={"chapters": [{"key": "limits"}]},
        approved_at="2026-08-17T00:00:00Z",
    )
    current_chapter = _chapter(record_id="chapter:latest", version_no=2)
    old_chapter = _chapter(record_id="chapter:old", version_no=1)
    old_chapter.course_version = "course_version:old"
    assert old_chapter.artifact is not None
    old_chapter.artifact["labs"] = [{"key": "old-limit-plot"}]
    current_lab = _lab(
        record_id="lab:current", chapter_id="chapter:latest", lab_key="limit-plot"
    )
    old_lab = _lab(
        record_id="lab:old",
        chapter_id="chapter:old",
        lab_key="old-limit-plot",
        version_id="course_version:old",
    )
    current_attempt = Attempt(
        id="attempt:current",
        lab="lab:current",
        answers={"answer": "0"},
        course="course:one",
        course_version="course_version:current",
        chapter="chapter:latest",
        chapter_key="limits",
        orphan_status="active",
    )
    old_attempt = Attempt(
        id="attempt:old",
        lab="lab:old",
        answers={"answer": "1"},
        course="course:one",
        course_version="course_version:old",
        chapter="chapter:old",
        chapter_key="limits",
        orphan_status="orphaned",
    )

    async def get_version(record_id: str) -> CourseVersion:
        return current_version if record_id == "course_version:current" else old_version

    async def get_lab(record_id: str) -> Lab:
        return current_lab if record_id == "lab:current" else old_lab

    async def get_chapter(record_id: str) -> Chapter:
        return current_chapter if record_id == "chapter:latest" else old_chapter

    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", get_version)
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[current_chapter])
    )
    monkeypatch.setattr(Lab, "get", get_lab)
    monkeypatch.setattr(Chapter, "get", get_chapter)
    monkeypatch.setattr(
        "api.course_service.repo_query",
        AsyncMock(
            return_value=[
                current_attempt.model_dump(mode="json"),
                old_attempt.model_dump(mode="json"),
            ]
        ),
    )

    result = await CourseService.list_chapter_attempts("course:one", "limits")

    assert [item["lab_key"] for item in result] == [
        "limit-plot",
        "old-limit-plot",
    ]
    assert [item["attempt"]["id"] for item in result] == [
        "attempt:current",
        "attempt:old",
    ]
    assert result[1]["attempt"]["orphan_status"] == "orphaned"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "chapterless_lab",
        "foreign_chapter",
        "unknown_lab_key",
        "unknown_exercise_key",
    ],
)
async def test_attempt_history_fails_closed_on_historical_ownership_corruption(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    course = _current_course()
    current_version = _current_version()
    history_version = CourseVersion(
        id="course_version:history",
        course="course:one",
        version_no=1,
        status="published",
        outline_artifact={"chapters": [{"key": "limits"}]},
        approved_at="2026-08-17T00:00:00Z",
    )
    current_chapter = _chapter(record_id="chapter:latest", version_no=2)
    history_chapter = _chapter(record_id="chapter:history", version_no=1)
    history_chapter.course_version = (
        "course_version:foreign"
        if corruption == "foreign_chapter"
        else "course_version:history"
    )
    history_chapter.artifact = {
        "labs": [
            {
                "key": (
                    "different-key"
                    if corruption == "unknown_lab_key"
                    else "history-plot"
                )
            }
        ],
        "exercises": [{"key": "history-core"}],
    }
    lab = _lab(
        record_id="lab:history",
        chapter_id="chapter:history",
        lab_key="history-plot",
        version_id="course_version:history",
    )
    if corruption == "chapterless_lab":
        lab.chapter = None
    attempt = Attempt(
        id="attempt:crafted",
        lab="lab:history",
        answers={"answer": "crafted"},
        course="course:one",
        course_version="course_version:history",
        chapter="chapter:history",
        chapter_key="limits",
        exercise_key=(
            "invented-exercise" if corruption == "unknown_exercise_key" else None
        ),
    )

    async def get_version(record_id: str) -> CourseVersion:
        return (
            current_version
            if record_id == "course_version:current"
            else history_version
        )

    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", get_version)
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[current_chapter])
    )
    monkeypatch.setattr(Lab, "get", AsyncMock(return_value=lab))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=history_chapter))
    monkeypatch.setattr(
        "api.course_service.repo_query",
        AsyncMock(return_value=[attempt.model_dump(mode="json")]),
    )

    with pytest.raises(NotFoundError, match="ownership"):
        await CourseService.list_chapter_attempts("course:one", "limits")


def test_chapter_lab_and_attempt_routes_use_stable_keys_and_strict_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lab = _lab(
        record_id="lab:current", chapter_id="chapter:latest", lab_key="limit-plot"
    )
    attempt = Attempt(
        id="attempt:one",
        lab="lab:current",
        answers={"answer": "0"},
        course="course:one",
        course_version="course_version:current",
        chapter="chapter:latest",
        chapter_key="limits",
    )
    lab_descriptor = {
        "id": "lab:current",
        "lab_key": "limit-plot",
        "lab_type": "function_plot",
        "spec": lab.payload,
    }
    attempt_descriptor = {
        "lab_key": "limit-plot",
        "attempt": {**attempt.model_dump(mode="json"), "id": "attempt:one"},
    }
    list_labs = AsyncMock(return_value=[lab_descriptor])
    list_attempts = AsyncMock(return_value=[attempt_descriptor])
    create_attempt = AsyncMock(return_value=attempt)
    monkeypatch.setattr(CourseService, "list_chapter_labs", list_labs)
    monkeypatch.setattr(CourseService, "list_chapter_attempts", list_attempts)
    monkeypatch.setattr(CourseService, "create_chapter_attempt", create_attempt)

    from api.main import app

    client = TestClient(app)
    labs = client.get("/api/courses/course:one/chapters/limits/labs")
    attempts = client.get("/api/courses/course:one/chapters/limits/attempts")
    submitted = client.post(
        "/api/courses/course:one/chapters/limits/labs/limit-plot/attempts",
        json={
            "answers": {"answer": "0"},
            "exercise_key": "limit-core",
            "answer": "zero",
            "hints_used": 2,
            "answer_revealed": True,
            "transfer_completed": False,
        },
    )
    rejected = client.post(
        "/api/courses/course:one/chapters/limits/labs/limit-plot/attempts",
        json={"answers": {}, "lab_id": "lab:current"},
    )

    assert labs.status_code == 200
    assert labs.json()[0]["lab_key"] == "limit-plot"
    assert attempts.status_code == 200
    assert attempts.json()[0]["attempt"]["id"] == "attempt:one"
    assert submitted.status_code == 201
    assert submitted.json()["lab"] == "lab:current"
    assert rejected.status_code == 422
    create_attempt.assert_awaited_once_with(
        "course:one",
        "limits",
        "limit-plot",
        {
            "answers": {"answer": "0"},
            "exercise_key": "limit-core",
            "answer": "zero",
            "hints_used": 2,
            "answer_revealed": True,
            "transfer_completed": False,
        },
    )
