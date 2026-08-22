from datetime import datetime, timezone
from typing import Any, cast, get_type_hints

import pytest
from pydantic import ValidationError

from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.authoring_service import AuthoringService
from open_notebook.course.learning_service import LearningService
from open_notebook.course.portability_service import PortabilityService
from open_notebook.course.publication_service import PublicationService
from open_notebook.course.task_backend import (
    CourseTaskRequest,
    SurrealCommandTaskBackend,
)
from open_notebook.course.tutor_service import TutorService
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    ExerciseBlueprint,
    GradedPayload,
    LearningEvent,
    NumericGraderSpec,
    ReplaceLabOperation,
    SymbolicGraderSpec,
    TransferTaskSpec,
    TutorResponse,
    TutorTurn,
)
from open_notebook.course.v2_models import (
    CourseExercise,
    CourseImmutableRecordError,
    CourseLearningEvent,
)


def difficulty() -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=3,
        symbolic_depth=2,
        representation_shifts=1,
        proof_burden=0,
        physics_constraints=0,
    )


def transfer() -> TransferTaskSpec:
    return TransferTaskSpec(
        key="limits-inverse",
        prompt="Construct a function from the requested limiting behavior.",
        invariant_concept_keys=["limit"],
        dimensions=["inverse_or_constructive"],
        difficulty=difficulty(),
        grader=SymbolicGraderSpec(
            kind="symbolic", expected_expression="x", allowed_symbols=["x"]
        ),
        anchor_ids=["anchor:limit"],
    )


def blueprint() -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key="limits-core",
        chapter_key="limits",
        prompt="Evaluate the source-grounded limit.",
        concept_keys=["limit"],
        exercise_type="worked_source",
        answer_type="symbolic",
        source_anchor_ids=["anchor:limit"],
        difficulty=difficulty(),
        grader=SymbolicGraderSpec(
            kind="symbolic", expected_expression="1", allowed_symbols=[]
        ),
        is_core=True,
        is_gating=True,
        is_source_level=True,
        transfer_task=transfer(),
    )


def test_nested_contract_values_are_deeply_immutable_and_json_serializable() -> None:
    event = LearningEvent(
        event_id="event-1",
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_key="limits",
        concept_key="limit",
        exercise_key="limits-core",
        kind="graded_correct",
        payload={
            "answer_revealed": False,
            "hints_used": 0,
            "response_parts": ["audit"],
        },
        occurred_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
    )

    assert isinstance(event.payload, GradedPayload)
    with pytest.raises(ValidationError, match="frozen"):
        event.payload.answer_revealed = True  # type: ignore[misc]
    with pytest.raises(AttributeError):
        cast(Any, event.payload.response_parts).append("changed")
    assert event.model_dump(mode="json")["payload"]["response_parts"] == ["audit"]


def test_answer_type_must_match_grader_kind_and_text_is_safe() -> None:
    payload = blueprint().model_dump()
    payload.update(
        {
            "answer_type": "proof",
            "grader": NumericGraderSpec(kind="numeric", expected="1"),
        }
    )
    with pytest.raises(ValidationError, match="answer_type"):
        ExerciseBlueprint.model_validate(payload)

    with pytest.raises(ValidationError, match="HTML"):
        ExerciseBlueprint.model_validate(
            {**blueprint().model_dump(), "prompt": "<script>alert(1)</script>"}
        )


def test_draft_lab_operation_uses_existing_safe_lab_union() -> None:
    with pytest.raises(ValidationError):
        ReplaceLabOperation(
            kind="replace_lab",
            block_key="lab-1",
            lab_spec={
                "kind": "function_plot",
                "key": "plot",
                "title": "Unsafe",
                "expressions": ["x"],
                "domain": {"x": [-1, 1]},
                "code": "alert(1)",
            },
        )


def test_learning_event_payload_is_typed_by_kind() -> None:
    with pytest.raises(ValidationError, match="payload"):
        LearningEvent(
            event_id="event-2",
            course_id="course:one",
            course_version_id="course_version:one",
            chapter_key="limits",
            concept_key="limit",
            exercise_key="limits-core",
            kind="hint_viewed",
            payload={"answer_revealed": True},
            occurred_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )


def test_tutor_can_refuse_without_citations_but_cited_answer_cannot_be_user_turn() -> None:
    refusal = TutorResponse(
        session_id="course_tutor_session:one",
        turn=TutorTurn(
            turn_no=1,
            role="assistant",
            content="The selected evidence is insufficient.",
            anchor_ids=[],
        ),
        insufficient_evidence=True,
    )
    assert refusal.insufficient_evidence is True

    with pytest.raises(ValidationError):
        TutorResponse(
            session_id="course_tutor_session:one",
            turn=TutorTurn(
                turn_no=2,
                role="assistant",
                content="An uncited factual answer.",
                anchor_ids=[],
            ),
            insufficient_evidence=False,
        )
    with pytest.raises(ValidationError):
        TutorResponse(
            session_id="course_tutor_session:one",
            turn=TutorTurn(
                turn_no=3,
                role="user",
                content="User text cannot be an assistant response.",
                anchor_ids=[],
            ),
            insufficient_evidence=True,
        )


def test_persistent_exercise_revalidates_the_strict_blueprint() -> None:
    with pytest.raises(ValidationError):
        CourseExercise(
            course="course:one",
            course_version="course_version:one",
            chapter="chapter:one",
            chapter_key="limits",
            exercise_key="limits-core",
            blueprint={**blueprint().model_dump(), "answer_type": "proof"},
            source_anchor_ids=["anchor:limit"],
            difficulty=difficulty(),
            grader=NumericGraderSpec(kind="numeric", expected="1"),
            is_core=True,
            is_gating=True,
            is_source_level=True,
        )


@pytest.mark.asyncio
async def test_learning_events_are_append_only() -> None:
    event = CourseLearningEvent(
        id="course_learning_event:one",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="limits",
        concept_key="limit",
        exercise_key="limits-core",
        event_key="event-1",
        kind="graded_correct",
        payload={"answer_revealed": False, "hints_used": 0},
        occurred_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
    )

    with pytest.raises(CourseImmutableRecordError):
        await event.save()
    with pytest.raises(CourseImmutableRecordError):
        await event.delete()


def test_task_request_and_six_service_boundaries_are_explicit() -> None:
    request = CourseTaskRequest(
        task="exercise_bank",
        idempotency_key="a" * 64,
        arguments=[{"name": "course_id", "value": "course:one"}],
    )
    assert request.task == "exercise_bank"
    assert get_type_hints(SurrealCommandTaskBackend.submit)["request"] is CourseTaskRequest
    assert {
        AuthoringService.__module__,
        AssessmentService.__module__,
        LearningService.__module__,
        TutorService.__module__,
        PublicationService.__module__,
        PortabilityService.__module__,
    } == {
        "open_notebook.course.authoring_service",
        "open_notebook.course.assessment_service",
        "open_notebook.course.learning_service",
        "open_notebook.course.tutor_service",
        "open_notebook.course.publication_service",
        "open_notebook.course.portability_service",
    }
