"""Strict HTTP and ownership contracts for the Course V2 learning API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.course_service import CourseConflictError, CourseService
from api.course_v2_service import CourseV2Service, course_v2_service
from api.models import CourseExerciseGradeRequest, CourseLearningEventRequest
from open_notebook.course.learning_service import LearningService
from open_notebook.course.models import Chapter, Course, CourseVersion
from open_notebook.course.v2_contracts import (
    ConceptMastery,
    DifficultyVector,
    ExerciseBlueprint,
    LearningEvent,
    NumericGraderSpec,
    ReviewQueueItem,
)
from open_notebook.course.v2_models import CourseExercise
from open_notebook.exceptions import InvalidInputError, NotFoundError

NOW = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app)


def _blueprint(*, advisory: bool = False) -> ExerciseBlueprint:
    values: dict[str, object] = {
        "key": "core-1",
        "chapter_key": "linear",
        "prompt": "Solve the source-grounded exercise.",
        "concept_keys": ["linear-equations"],
        "exercise_type": "generated_core",
        "answer_type": "numeric",
        "source_anchor_ids": ["anchor:linear"],
        "difficulty": DifficultyVector(
            concept_count=1,
            reasoning_steps=2,
            symbolic_depth=1,
            representation_shifts=0,
            proof_burden=0,
            physics_constraints=0,
        ),
        "grader": NumericGraderSpec(kind="numeric", expected="4"),
        "is_core": True,
        "is_gating": True,
        "is_source_level": False,
        "transfer_task": {
            "key": "core-1-transfer",
            "prompt": "Apply the invariant in a changed representation.",
            "invariant_concept_keys": ["linear-equations"],
            "dimensions": ["representation"],
            "answer_type": "numeric",
            "difficulty": {
                "concept_count": 1,
                "reasoning_steps": 2,
                "symbolic_depth": 1,
                "representation_shifts": 1,
                "proof_burden": 0,
                "physics_constraints": 0,
            },
            "grader": {"kind": "numeric", "expected": "8"},
            "anchor_ids": ["anchor:linear"],
        },
    }
    if advisory:
        values.update(
            answer_type="explanation",
            grader={
                "kind": "advisory",
                "rubric": "Explain the reasoning using the cited source.",
                "grants_mastery": False,
            },
            is_core=False,
            is_gating=False,
            transfer_task=None,
        )
    return ExerciseBlueprint.model_validate(values)


def _exercise(*, course_id: str = "course:abc", advisory: bool = False) -> CourseExercise:
    blueprint = _blueprint(advisory=advisory)
    return CourseExercise(
        id="course_exercise:one",
        course=course_id,
        course_version="course_version:published",
        chapter="chapter:published",
        chapter_key=blueprint.chapter_key,
        exercise_key=blueprint.key,
        blueprint=blueprint,
        source_anchor_ids=blueprint.source_anchor_ids,
        difficulty=blueprint.difficulty,
        grader=blueprint.grader,
        is_core=blueprint.is_core,
        is_gating=blueprint.is_gating,
        is_source_level=blueprint.is_source_level,
    )


def _scope() -> tuple[CourseVersion, Chapter]:
    return (
        CourseVersion(
            id="course_version:published",
            course="course:abc",
            version_no=2,
            status="published",
        ),
        Chapter(
            id="chapter:published",
            course_version="course_version:published",
            chapter_no=1,
            chapter_key="linear",
            title="Linear equations",
            status="published",
        ),
    )


def _mastery() -> ConceptMastery:
    return ConceptMastery(
        course_id="course:abc",
        course_version_id="course_version:published",
        chapter_key="linear",
        concept_key="linear-equations",
        status="practiced",
        successful_exercise_keys=["core-1"],
        unrevealed_success_count=1,
        review_level=0,
        review_due_at=None,
        last_event_at=NOW,
        snapshot_hash="a" * 64,
    )


def test_grade_uses_stable_key_and_rejects_client_record_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    grade = AsyncMock()
    monkeypatch.setattr(course_v2_service, "grade_exercise", grade)

    response = client.post(
        "/api/courses/course:abc/exercises/core-1/grade",
        json={
            "chapter_key": "linear",
            "concept_key": "linear-equations",
            "attempt_key": "attempt-1",
            "answer": {"value": "2"},
            "exercise_id": "course_exercise:foreign",
        },
    )

    assert response.status_code == 422
    grade.assert_not_awaited()


def test_grade_path_and_exercise_query_reject_record_ids(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    grade = AsyncMock()
    exercises = AsyncMock()
    monkeypatch.setattr(course_v2_service, "grade_exercise", grade)
    monkeypatch.setattr(course_v2_service, "list_exercises", exercises)

    grade_response = client.post(
        "/api/courses/course:abc/exercises/course_exercise:foreign/grade",
        json={
            "chapter_key": "linear",
            "concept_key": "linear-equations",
            "attempt_key": "attempt-1",
            "answer": "4",
        },
    )
    list_response = client.get(
        "/api/courses/course:abc/exercises?chapter_key=chapter:foreign"
    )

    assert grade_response.status_code == 422
    assert list_response.status_code == 422
    grade.assert_not_awaited()
    exercises.assert_not_awaited()


def test_grade_route_calls_facade_with_only_stable_scope(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    grade = AsyncMock(
        return_value={
            "grade": {
                "correct": True,
                "advisory": False,
                "grants_mastery": True,
                "feedback_code": "correct",
                "part_results": [],
            },
            "mastery": _mastery().model_dump(mode="json"),
            "event_key": "grade-event-1",
        }
    )
    monkeypatch.setattr(course_v2_service, "grade_exercise", grade)

    response = client.post(
        "/api/courses/course:abc/exercises/core-1/grade",
        json={
            "chapter_key": "linear",
            "concept_key": "linear-equations",
            "attempt_key": "attempt-1",
            "answer": "4",
        },
    )

    assert response.status_code == 200
    assert response.json()["grade"]["correct"] is True
    grade_call = grade.await_args
    assert grade_call is not None
    request = grade_call.args[2]
    assert grade_call.args[:2] == ("course:abc", "core-1")
    assert request.chapter_key == "linear"
    assert not hasattr(request, "course_version_id")


def test_learning_event_rejects_client_version_and_malformed_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    append = AsyncMock()
    monkeypatch.setattr(course_v2_service, "append_learning_event", append)

    injected = client.post(
        "/api/courses/course:abc/learning/events",
        json={
            "idempotency_key": "open-1",
            "chapter_key": "linear",
            "kind": "chapter_opened",
            "payload": {"block_key": None},
            "course_version_id": "course_version:foreign",
        },
    )
    malformed = client.post(
        "/api/courses/course:abc/learning/events",
        json={
            "idempotency_key": "position-1",
            "chapter_key": "linear",
            "kind": "reading_position",
            "payload": {"block_key": None},
        },
    )

    assert injected.status_code == 422
    assert malformed.status_code == 422
    append.assert_not_awaited()


def test_learning_read_routes_return_only_public_v2_contracts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    overview = AsyncMock(
        return_value={
            "course_id": "course:abc",
            "course_version_id": "course_version:published",
            "chapters": [],
            "masteries": [],
            "review_queue": [],
        }
    )
    review_queue = AsyncMock(return_value=[])
    exercises = AsyncMock(return_value=[_blueprint()])
    monkeypatch.setattr(course_v2_service, "get_learning_overview", overview)
    monkeypatch.setattr(course_v2_service, "get_review_queue", review_queue)
    monkeypatch.setattr(course_v2_service, "list_exercises", exercises)

    overview_response = client.get(
        "/api/courses/course:abc/learning/overview"
    )
    queue_response = client.get(
        "/api/courses/course:abc/learning/review-queue"
    )
    exercise_response = client.get(
        "/api/courses/course:abc/exercises?chapter_key=linear"
    )

    assert overview_response.status_code == 200
    assert overview_response.json()["course_version_id"] == (
        "course_version:published"
    )
    assert queue_response.status_code == 200
    assert queue_response.json() == []
    assert exercise_response.status_code == 200
    assert exercise_response.json()[0]["key"] == "core-1"
    assert "id" not in exercise_response.json()[0]
    assert "grader" not in exercise_response.json()[0]
    assert "transfer_task" not in exercise_response.json()[0]
    exercises.assert_awaited_once_with("course:abc", "linear")


def test_learning_event_route_passes_server_scope_to_facade(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    append = AsyncMock(
        return_value={
            "event": {
                "event_id": "open-1",
                "course_id": "course:abc",
                "course_version_id": "course_version:published",
                "chapter_key": "linear",
                "concept_key": None,
                "exercise_key": None,
                "kind": "chapter_opened",
                "payload": {"block_key": None},
                "occurred_at": NOW.isoformat(),
            },
            "mastery": None,
        }
    )
    monkeypatch.setattr(course_v2_service, "append_learning_event", append)

    response = client.post(
        "/api/courses/course:abc/learning/events",
        json={
            "idempotency_key": "open-1",
            "chapter_key": "linear",
            "kind": "chapter_opened",
            "payload": {"block_key": None},
        },
    )

    assert response.status_code == 200
    assert response.json()["event"]["course_version_id"] == (
        "course_version:published"
    )
    append_call = append.await_args
    assert append_call is not None
    assert append_call.args[0] == "course:abc"
    assert append_call.args[1].idempotency_key == "open-1"


@pytest.mark.asyncio
async def test_grade_resolves_scope_and_server_authors_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    append_event = AsyncMock(return_value=_mastery())
    learning = cast(
        LearningService,
        SimpleNamespace(append_event=append_event),
    )
    service = CourseV2Service(learning_service=learning, clock=lambda: NOW)
    offload = AsyncMock(side_effect=lambda function, *args: function(*args))
    monkeypatch.setattr("api.course_v2_service.asyncio.to_thread", offload)
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=_scope()),
    )
    monkeypatch.setattr(
        CourseService,
        "get_learning_event",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        CourseService,
        "get_current_exercise",
        AsyncMock(return_value=_exercise()),
    )

    result = await service.grade_exercise(
        "course:abc",
        "core-1",
        CourseExerciseGradeRequest(
            chapter_key="linear",
            concept_key="linear-equations",
            attempt_key="attempt-1",
            answer="4",
        ),
    )

    assert result.grade.correct is True
    assert result.mastery == _mastery()
    append_call = append_event.await_args
    assert append_call is not None
    event = append_call.args[0]
    assert event.course_id == "course:abc"
    assert event.course_version_id == "course_version:published"
    assert event.chapter_key == "linear"
    assert event.exercise_key == "core-1"
    assert event.kind == "graded_correct"
    offload.assert_awaited_once()


@pytest.mark.asyncio
async def test_grade_retry_reuses_server_event_and_rejects_changed_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: LearningEvent | None = None

    async def get_event(_course_id: str, _event_key: str):
        return stored

    async def append_event(event: LearningEvent):
        nonlocal stored
        if stored is None:
            stored = event
        return _mastery()

    append = AsyncMock(side_effect=append_event)
    learning = cast(
        LearningService,
        SimpleNamespace(append_event=append),
    )
    moments = iter((NOW, NOW + timedelta(seconds=2), NOW + timedelta(seconds=4)))
    service = CourseV2Service(learning_service=learning, clock=lambda: next(moments))
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=_scope()),
    )
    monkeypatch.setattr(
        CourseService,
        "get_current_exercise",
        AsyncMock(return_value=_exercise()),
    )
    monkeypatch.setattr(CourseService, "get_learning_event", get_event)
    request = CourseExerciseGradeRequest(
        chapter_key="linear",
        concept_key="linear-equations",
        attempt_key="attempt-retry",
        answer="4",
    )

    first = await service.grade_exercise("course:abc", "core-1", request)
    second = await service.grade_exercise("course:abc", "core-1", request)

    assert first.event_key == second.event_key
    assert first.event_key is not None and first.event_key.startswith("grade-")
    first_event = append.await_args_list[0].args[0]
    second_event = append.await_args_list[1].args[0]
    assert first_event == second_event
    with pytest.raises(InvalidInputError, match="already graded"):
        await service.grade_exercise(
            "course:abc",
            "core-1",
            request.model_copy(update={"answer": "5"}),
        )
    assert append.await_count == 2


@pytest.mark.asyncio
async def test_advisory_grade_never_writes_mastery_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    append_event = AsyncMock()
    learning = cast(
        LearningService,
        SimpleNamespace(append_event=append_event),
    )
    service = CourseV2Service(learning_service=learning, clock=lambda: NOW)
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=_scope()),
    )
    monkeypatch.setattr(
        CourseService,
        "get_current_exercise",
        AsyncMock(return_value=_exercise(advisory=True)),
    )

    result = await service.grade_exercise(
        "course:abc",
        "core-1",
        CourseExerciseGradeRequest(
            chapter_key="linear",
            concept_key="linear-equations",
            attempt_key="attempt-1",
            answer="A reasoned explanation.",
        ),
    )

    assert result.grade.advisory is True
    assert result.mastery is None
    assert result.event_key is None
    append_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_exercise_is_rejected_before_event_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    append_event = AsyncMock()
    learning = cast(
        LearningService,
        SimpleNamespace(append_event=append_event),
    )
    service = CourseV2Service(learning_service=learning, clock=lambda: NOW)
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=_scope()),
    )
    monkeypatch.setattr(
        CourseService,
        "get_current_exercise",
        AsyncMock(return_value=_exercise(course_id="course:foreign")),
    )

    with pytest.raises(InvalidInputError, match="Course scope"):
        await service.grade_exercise(
            "course:abc",
            "core-1",
            CourseExerciseGradeRequest(
                chapter_key="linear",
                concept_key="linear-equations",
                attempt_key="attempt-1",
                answer="4",
            ),
        )
    append_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_activity_event_uses_activity_pipeline_without_mastery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    append_activity_event = AsyncMock(side_effect=lambda event: event)
    append_event = AsyncMock()
    learning = cast(
        LearningService,
        SimpleNamespace(
            append_activity_event=append_activity_event,
            append_event=append_event,
        ),
    )
    service = CourseV2Service(learning_service=learning, clock=lambda: NOW)
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=_scope()),
    )
    monkeypatch.setattr(
        CourseService,
        "get_learning_event",
        AsyncMock(return_value=None),
    )

    result = await service.append_learning_event(
        "course:abc",
        CourseLearningEventRequest(
            idempotency_key="position-1",
            chapter_key="linear",
            kind="reading_position",
            payload={"block_key": "definition-1"},
        ),
    )

    assert result.mastery is None
    assert result.event.kind == "reading_position"
    assert result.event.event_id != "position-1"
    assert result.event.event_id.startswith("action-")
    assert result.event.occurred_at == NOW
    append_activity_event.assert_awaited_once()
    append_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_action_retry_reuses_server_identity_and_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: LearningEvent | None = None

    async def get_event(_course_id: str, _event_key: str):
        return stored

    async def append_activity(event: LearningEvent):
        nonlocal stored
        if stored is None:
            stored = event
        return stored

    append = AsyncMock(side_effect=append_activity)
    learning = cast(
        LearningService,
        SimpleNamespace(append_activity_event=append),
    )
    service = CourseV2Service(learning_service=learning, clock=lambda: NOW)
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=_scope()),
    )
    monkeypatch.setattr(CourseService, "get_learning_event", get_event)
    request = CourseLearningEventRequest(
        idempotency_key="position-retry",
        chapter_key="linear",
        kind="reading_position",
        payload={"block_key": "definition-1"},
    )

    first = await service.append_learning_event("course:abc", request)
    second = await service.append_learning_event("course:abc", request)

    assert first.event == second.event
    assert first.event.occurred_at == NOW
    assert first.event.event_id.startswith("action-")
    with pytest.raises(InvalidInputError, match="Idempotency key"):
        await service.append_learning_event(
            "course:abc",
            request.model_copy(
                update={"payload": {"block_key": "definition-2"}}
            ),
        )
    assert append.await_count == 2


@pytest.mark.asyncio
async def test_public_exercise_keeps_transfer_prompt_but_withholds_oracles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, _chapter = _scope()
    service = CourseV2Service()
    monkeypatch.setattr(
        CourseService,
        "list_current_exercises",
        AsyncMock(return_value=(version, (_exercise(),))),
    )

    exercises = await service.list_exercises("course:abc", "linear")

    public = exercises[0].model_dump(mode="json")
    assert public["transfer"]["prompt"].startswith("Apply the invariant")
    assert public["transfer"]["answer_type"] == "numeric"
    assert "grader" not in public
    assert "grader" not in public["transfer"]
    assert "change_evidence" not in public["transfer"]


@pytest.mark.asyncio
async def test_overview_reads_mastery_after_review_queue_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    state = {"replayed": False}

    async def review_queue(_course_id: str, _now: datetime):
        state["replayed"] = True
        return [
            ReviewQueueItem(
                chapter_key="linear",
                concept_key="linear-equations",
                status="review_due",
                due_at=NOW,
                interval_days=1,
            )
        ]

    async def list_masteries(_course_id: str):
        mastery = _mastery().model_copy(
            update={
                "status": "review_due" if state["replayed"] else "mastered",
                "review_due_at": NOW,
            }
        )
        return version, (mastery,)

    learning = cast(
        LearningService,
        SimpleNamespace(
            review_queue=review_queue,
            latest_reading_position=AsyncMock(return_value=None),
        ),
    )
    service = CourseV2Service(learning_service=learning, clock=lambda: NOW)
    monkeypatch.setattr(
        CourseService,
        "list_current_published_chapters",
        AsyncMock(return_value=(version, (chapter,))),
    )
    monkeypatch.setattr(
        CourseService,
        "list_current_masteries",
        list_masteries,
    )
    monkeypatch.setattr(
        CourseService,
        "confirm_current_published_scope",
        AsyncMock(),
    )

    result = await service.get_learning_overview("course:abc")

    assert result.masteries[0].status == "review_due"
    assert result.review_queue[0].status == "review_due"


@pytest.mark.asyncio
async def test_overview_rejects_reading_position_from_switched_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    foreign_position = LearningEvent(
        event_id="position-new-version",
        course_id="course:abc",
        course_version_id="course_version:new",
        chapter_key="linear",
        kind="reading_position",
        payload={"block_key": "definition-1"},
        occurred_at=NOW,
    )
    learning = cast(
        LearningService,
        SimpleNamespace(
            review_queue=AsyncMock(return_value=[]),
            latest_reading_position=AsyncMock(return_value=foreign_position),
        ),
    )
    service = CourseV2Service(learning_service=learning, clock=lambda: NOW)
    monkeypatch.setattr(
        CourseService,
        "list_current_published_chapters",
        AsyncMock(return_value=(version, (chapter,))),
    )
    monkeypatch.setattr(
        CourseService,
        "list_current_masteries",
        AsyncMock(return_value=(version, ())),
    )
    monkeypatch.setattr(
        CourseService,
        "confirm_current_published_scope",
        AsyncMock(),
    )

    with pytest.raises(InvalidInputError, match="reading position"):
        await service.get_learning_overview("course:abc")


@pytest.mark.asyncio
async def test_overview_rejects_mastery_from_newly_published_chapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    foreign_mastery = _mastery().model_copy(
        update={"chapter_key": "new-chapter", "concept_key": "new-concept"}
    )
    learning = cast(
        LearningService,
        SimpleNamespace(
            review_queue=AsyncMock(return_value=[]),
            latest_reading_position=AsyncMock(return_value=None),
        ),
    )
    service = CourseV2Service(learning_service=learning, clock=lambda: NOW)
    monkeypatch.setattr(
        CourseService,
        "list_current_published_chapters",
        AsyncMock(return_value=(version, (chapter,))),
    )
    monkeypatch.setattr(
        CourseService,
        "list_current_masteries",
        AsyncMock(return_value=(version, (foreign_mastery,))),
    )
    monkeypatch.setattr(
        CourseService,
        "confirm_current_published_scope",
        AsyncMock(),
    )

    with pytest.raises(InvalidInputError, match="chapter scope"):
        await service.get_learning_overview("course:abc")


@pytest.mark.asyncio
async def test_exercise_read_rejects_version_switch_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    newer = version.model_copy(
        update={"id": "course_version:new", "version_no": 3}
    )
    monkeypatch.setattr(
        CourseService,
        "list_current_published_chapters",
        AsyncMock(return_value=(version, (chapter,))),
    )
    monkeypatch.setattr(
        CourseService,
        "get_current_published_version",
        AsyncMock(return_value=newer),
    )
    monkeypatch.setattr("api.course_service.repo_query", AsyncMock(return_value=[]))

    with pytest.raises(CourseConflictError, match="changed"):
        await CourseService.list_current_exercises("course:abc")


@pytest.mark.asyncio
async def test_exercise_read_rechecks_chapter_and_pointer_after_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    newer = version.model_copy(
        update={"id": "course_version:new", "version_no": 3}
    )
    monkeypatch.setattr(
        CourseService,
        "list_current_published_chapters",
        AsyncMock(return_value=(version, (chapter,))),
    )
    monkeypatch.setattr("api.course_service.repo_query", AsyncMock(return_value=[]))

    monkeypatch.setattr(
        CourseService,
        "get_current_published_version",
        AsyncMock(return_value=version),
    )
    monkeypatch.setattr(
        CourseVersion,
        "chapters",
        AsyncMock(return_value=[chapter.model_copy(update={"status": "ready"})]),
    )
    with pytest.raises(CourseConflictError, match="chapter scope changed"):
        await CourseService.list_current_exercises("course:abc")

    monkeypatch.setattr(
        CourseService,
        "get_current_published_version",
        AsyncMock(side_effect=(version, newer)),
    )
    monkeypatch.setattr(
        CourseVersion,
        "chapters",
        AsyncMock(return_value=[chapter]),
    )
    with pytest.raises(CourseConflictError, match="version changed"):
        await CourseService.list_current_exercises("course:abc")


@pytest.mark.asyncio
async def test_current_version_rejects_foreign_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course = Course(
        id="course:abc",
        title="Algebra",
        notebook="notebook:one",
        outline_version_id="course_version:foreign",
    )
    monkeypatch.setattr(
        CourseService, "get_course", AsyncMock(return_value=course)
    )
    monkeypatch.setattr(
        "api.course_service._typed_get",
        AsyncMock(
            return_value=CourseVersion(
                id="course_version:foreign",
                course="course:foreign",
                version_no=1,
                status="published",
            )
        ),
    )

    with pytest.raises(CourseConflictError, match="does not belong"):
        await CourseService.get_current_published_version("course:abc")


@pytest.mark.asyncio
async def test_unpublished_chapter_is_not_a_learning_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = CourseVersion(
        id="course_version:published",
        course="course:abc",
        version_no=2,
        status="published",
    )
    monkeypatch.setattr(
        CourseService,
        "get_current_published_version",
        AsyncMock(return_value=version),
    )
    monkeypatch.setattr(
        CourseVersion,
        "chapters",
        AsyncMock(
            return_value=[
                Chapter(
                    id="chapter:draft",
                    course_version="course_version:published",
                    chapter_no=1,
                    chapter_key="linear",
                    title="Linear equations",
                    status="ready",
                )
            ]
        ),
    )

    with pytest.raises(NotFoundError, match="published chapter"):
        await CourseService.resolve_current_published_chapter(
            "course:abc", "linear"
        )


def test_learning_request_rejects_record_ids_and_invalid_transition_shape() -> None:
    with pytest.raises(ValueError):
        CourseLearningEventRequest.model_validate(
            {
                "idempotency_key": "hint-1",
                "chapter_key": "linear",
                "kind": "hint_viewed",
                "payload": {"attempt_key": "attempt-1", "hint_index": 1},
                "exercise_id": "course_exercise:one",
            }
        )
    with pytest.raises(ValueError):
        CourseLearningEventRequest.model_validate(
            {
                "idempotency_key": "hint-1",
                "chapter_key": "linear",
                "kind": "hint_viewed",
                "payload": {"attempt_key": "attempt-1", "hint_index": 1},
            }
        )


def test_learning_request_rejects_client_event_identity_and_timestamp() -> None:
    with pytest.raises(ValueError):
        CourseLearningEventRequest.model_validate(
            {
                "idempotency_key": "open-1",
                "event_key": "client-event",
                "chapter_key": "linear",
                "kind": "chapter_opened",
                "payload": {"block_key": None},
            }
        )
    with pytest.raises(ValueError):
        CourseLearningEventRequest.model_validate(
            {
                "idempotency_key": "open-1",
                "chapter_key": "linear",
                "kind": "chapter_opened",
                "payload": {"block_key": None},
                "occurred_at": NOW.isoformat(),
            }
        )
