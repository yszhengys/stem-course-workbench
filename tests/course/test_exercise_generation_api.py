from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.course_command_service import CourseCommandService, CourseJobSubmission
from api.models import CourseExerciseBankGenerateRequest
from open_notebook.course.contracts import CourseOutlineArtifact, ModelSelection
from open_notebook.course.models import (
    Chapter,
    Course,
    CourseGenerationRun,
    CourseVersion,
)
from open_notebook.course.workflow_service import CourseWorkflowService
from open_notebook.exceptions import NotFoundError


@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app)


def _selection(model: str) -> ModelSelection:
    return ModelSelection(adapter="ollama", model=model)


def _outline() -> CourseOutlineArtifact:
    return CourseOutlineArtifact.model_validate(
        {
            "title": "Algebra",
            "chapters": [
                {
                    "key": "linear-equations",
                    "title": "Linear equations",
                    "purpose": "Solve equations.",
                    "objective_keys": ["linear"],
                    "anchor_ids": ["anchor:one"],
                    "lab_keys": ["linear-plot"],
                }
            ],
            "concepts": [
                {
                    "key": "linear",
                    "label": "Linear equations",
                    "anchor_ids": ["anchor:one"],
                }
            ],
        }
    )


def _scope() -> tuple[Course, CourseVersion, Chapter]:
    course = Course(
        id="course:one",
        title="Algebra",
        notebook="notebook:one",
        status="generating",
        outline_version_id="course_version:one",
    )
    version = CourseVersion(
        id="course_version:one",
        course="course:one",
        version_no=1,
        status="generating",
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="linear-equations",
        title="Linear equations",
        status="ready",
        input_hash="a" * 64,
    )
    return course, version, chapter


@pytest.mark.parametrize(
    "payload",
    [
        {
            "anchor_ids": ["anchor:one"],
            "model": {"adapter": "ollama", "model": "qwen3.5:9b"},
        },
        {
            "anchor_ids": ["anchor:one", "anchor:one"],
            "model": {"adapter": "ollama", "model": "qwen3.5:9b"},
            "review_model": {"adapter": "ollama", "model": "gpt-oss:20b"},
        },
        {
            "anchor_ids": ["anchor:one"],
            "model": {"adapter": "ollama", "model": "qwen3.5:9b"},
            "review_model": {"adapter": "ollama", "model": "gpt-oss:20b"},
            "prompt": "untrusted prompt",
        },
    ],
)
def test_exercise_generation_request_is_strict(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CourseExerciseBankGenerateRequest.model_validate(payload)


def test_exercise_generation_defaults_to_versioned_v2_prompt() -> None:
    request = CourseExerciseBankGenerateRequest(
        anchor_ids=["anchor:one"],
        model=_selection("qwen3.5:9b"),
        review_model=_selection("gpt-oss:20b"),
    )

    assert request.prompt_version == "v2"


def test_exercise_generation_route_submits_both_explicit_models(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import api.routers.course as router_module

    submit = AsyncMock(
        return_value=CourseJobSubmission(
            command_id="command:one",
            run_id="course_generation_run:one",
            status="queued",
        )
    )
    monkeypatch.setattr(router_module.course_commands, "submit_exercise_bank", submit)
    payload = {
        "anchor_ids": ["anchor:one"],
        "prompt_version": "v2",
        "model": {"adapter": "ollama", "model": "qwen3.5:9b"},
        "review_model": {"adapter": "ollama", "model": "gpt-oss:20b"},
        "force": False,
    }

    response = client.post(
        "/api/courses/course:one/chapters/linear-equations/exercises/generate",
        json=payload,
    )

    assert response.status_code == 202
    assert response.json() == {
        "command_id": "command:one",
        "run_id": "course_generation_run:one",
        "status": "queued",
    }
    submit.assert_awaited_once_with(
        course_id="course:one",
        chapter_key="linear-equations",
        anchor_ids=["anchor:one"],
        prompt_version="v2",
        model=_selection("qwen3.5:9b"),
        review_model=_selection("gpt-oss:20b"),
        force=False,
    )


def test_exercise_build_status_route_returns_current_scoped_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import api.routers.course as router_module

    status = AsyncMock(
        return_value={
            "run_id": "course_generation_run:one",
            "command_id": "command:one",
            "status": "running",
            "error_message": None,
            "exercise_count": 0,
        }
    )
    monkeypatch.setattr(
        router_module.course_v2_service, "exercise_build_status", status
    )

    response = client.get(
        "/api/courses/course:one/chapters/linear-equations/exercises/build-status"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    status.assert_awaited_once_with("course:one", "linear-equations")


@pytest.mark.asyncio
async def test_submit_exercise_bank_binds_current_chapter_and_both_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.course_command_service as module

    course, version, chapter = _scope()
    submit = AsyncMock(
        return_value=CourseJobSubmission(
            command_id="command:one",
            run_id="course_generation_run:one",
            status="queued",
        )
    )
    service = CourseCommandService()
    monkeypatch.setattr(
        service,
        "_grounded",
        AsyncMock(return_value=(course, {"source:one": "b" * 64}, [])),
    )
    monkeypatch.setattr(
        CourseWorkflowService,
        "approved_version",
        AsyncMock(return_value=(version, _outline())),
    )
    monkeypatch.setattr(
        CourseWorkflowService,
        "resolve_current_chapter",
        AsyncMock(return_value=chapter),
    )
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[chapter])
    )
    selectable = AsyncMock()
    monkeypatch.setattr(module, "ensure_course_models_selectable", selectable)
    monkeypatch.setattr(service, "submit_stage", submit)

    result = await service.submit_exercise_bank(
        course_id="course:one",
        chapter_key="linear-equations",
        anchor_ids=["anchor:one"],
        prompt_version="v2",
        model=_selection("qwen3.5:9b"),
        review_model=_selection("gpt-oss:20b"),
    )

    assert result.run_id == "course_generation_run:one"
    selectable.assert_awaited_once_with(
        [_selection("qwen3.5:9b"), _selection("gpt-oss:20b")]
    )
    call = submit.await_args.kwargs
    assert call["stage"] == "exercise_bank"
    assert call["command_name"] == "course_generate_exercise_bank"
    assert call["course_version_id"] == "course_version:one"
    assert call["chapter_id"] == "chapter:one"
    assert call["command_args"]["review_model"] == _selection(
        "gpt-oss:20b"
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_exercise_submission_dedupes_active_run_and_force_creates_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.course_command_service as module
    from tests.course.test_course_command_orchestration import _FakeQueueStore

    course, version, chapter = _scope()
    store = _FakeQueueStore()
    service = CourseCommandService()
    monkeypatch.setattr(module, "repo_query", store.query)
    monkeypatch.setattr(module, "submit_command", store.submit)
    monkeypatch.setattr(
        service,
        "_grounded",
        AsyncMock(return_value=(course, {"source:one": "b" * 64}, [])),
    )
    monkeypatch.setattr(
        CourseWorkflowService,
        "approved_version",
        AsyncMock(return_value=(version, _outline())),
    )
    monkeypatch.setattr(
        CourseWorkflowService,
        "resolve_current_chapter",
        AsyncMock(return_value=chapter),
    )
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[chapter])
    )
    monkeypatch.setattr(
        module, "ensure_course_models_selectable", AsyncMock()
    )
    arguments = {
        "course_id": "course:one",
        "chapter_key": "linear-equations",
        "anchor_ids": ["anchor:one"],
        "prompt_version": "v2",
        "model": _selection("qwen3.5:9b"),
        "review_model": _selection("gpt-oss:20b"),
    }

    first = await service.submit_exercise_bank(**arguments)
    replay = await service.submit_exercise_bank(**arguments)
    forced = await service.submit_exercise_bank(**arguments, force=True)

    assert replay == first
    assert forced.run_id != first.run_id
    assert store.submit_count == 2
    assert len(store.runs) == 2


@pytest.mark.asyncio
async def test_build_status_rejects_a_run_from_another_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.course_command_service as module

    course, version, chapter = _scope()
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(
        CourseWorkflowService,
        "approved_version",
        AsyncMock(return_value=(version, _outline())),
    )
    monkeypatch.setattr(
        CourseWorkflowService,
        "resolve_current_chapter",
        AsyncMock(return_value=chapter),
    )
    foreign = CourseGenerationRun(
        id="course_generation_run:foreign",
        course="course:other",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="linear-equations",
        stage="exercise_bank",
        adapter="ollama",
        model="qwen3.5:9b",
        status="succeeded",
        prompt_version="v2",
        input_hash="f" * 64,
        output_hash="e" * 64,
    )
    monkeypatch.setattr(
        module,
        "repo_query",
        AsyncMock(return_value=[foreign.model_dump(mode="json")]),
    )

    with pytest.raises(NotFoundError):
        await CourseCommandService().exercise_build_status(
            "course:one", "linear-equations"
        )


@pytest.mark.asyncio
async def test_worker_permanent_exercise_failure_is_synchronized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import commands.course_commands as module

    request = module.CourseExerciseBankInput.model_validate(
        {
            "run_id": "course_generation_run:one",
            "course_id": "course:one",
            "chapter_key": "linear-equations",
            "anchor_ids": ["anchor:one"],
            "prompt_version": "v2",
            "model": _selection("qwen3.5:9b"),
            "review_model": _selection("gpt-oss:20b"),
        }
    )
    selectable = AsyncMock()
    generate = AsyncMock(side_effect=ValueError("stale chapter"))
    permanent = AsyncMock()
    run = CourseGenerationRun(
        id="course_generation_run:one",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="linear-equations",
        stage="exercise_bank",
        adapter="ollama",
        model="qwen3.5:9b",
        status="queued",
        prompt_version="v2",
        input_hash="a" * 64,
    )
    monkeypatch.setattr(module, "ensure_course_models_selectable", selectable)
    monkeypatch.setattr(
        module.CourseGenerationRun, "get", AsyncMock(return_value=run)
    )
    monkeypatch.setattr(module._exercise_workflow, "generate_and_persist", generate)
    monkeypatch.setattr(module, "_permanent_failure", permanent)

    with pytest.raises(ValueError, match="stale chapter"):
        await module.course_generate_exercise_bank_command(request)

    selectable.assert_awaited_once_with([request.model, request.review_model])
    permanent.assert_awaited_once()
    assert generate.await_args.kwargs["review_model"] == request.review_model
