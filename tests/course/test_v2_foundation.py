from unittest.mock import AsyncMock

import pytest

import open_notebook.course.task_backend as task_backend_module
from open_notebook.course.task_backend import (
    CourseTaskArgument,
    CourseTaskCancellationError,
    CourseTaskRequest,
    SurrealCommandTaskBackend,
)
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    ExerciseBlueprint,
    SymbolicGraderSpec,
    TransferTaskSpec,
)
from open_notebook.course.v2_models import (
    CourseConceptMastery,
    CourseDraftRevision,
    CourseExercise,
    CourseExport,
    CourseLearningEvent,
    CourseTutorSession,
    CourseTutorTurn,
)


def blueprint() -> ExerciseBlueprint:
    difficulty = DifficultyVector(
        concept_count=1,
        reasoning_steps=3,
        symbolic_depth=2,
        representation_shifts=1,
        proof_burden=0,
        physics_constraints=0,
    )
    grader = SymbolicGraderSpec(
        kind="symbolic", expected_expression="1", allowed_symbols=[]
    )
    return ExerciseBlueprint(
        key="limits-core-1",
        chapter_key="limits",
        prompt="Evaluate the source-grounded limit.",
        concept_keys=["limit"],
        exercise_type="worked_source",
        answer_type="symbolic",
        source_anchor_ids=["anchor:one"],
        difficulty=difficulty,
        grader=grader,
        is_core=True,
        is_gating=True,
        is_source_level=True,
        transfer_task=TransferTaskSpec(
            key="limits-inverse",
            prompt="Construct a function with the requested limiting behavior.",
            invariant_concept_keys=["limit"],
            dimensions=["inverse_or_constructive"],
            answer_type="symbolic",
            difficulty=difficulty,
            grader=grader,
            anchor_ids=["anchor:one"],
        ),
    )


def test_v2_models_map_one_to_one_to_migration_26_tables() -> None:
    assert {
        CourseExercise.table_name,
        CourseLearningEvent.table_name,
        CourseConceptMastery.table_name,
        CourseTutorSession.table_name,
        CourseTutorTurn.table_name,
        CourseDraftRevision.table_name,
        CourseExport.table_name,
    } == {
        "course_exercise",
        "course_learning_event",
        "course_concept_mastery",
        "course_tutor_session",
        "course_tutor_turn",
        "course_draft_revision",
        "course_export",
    }


def test_course_exercise_serializes_owned_record_fields() -> None:
    contract = blueprint()
    exercise = CourseExercise(
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key=contract.chapter_key,
        exercise_key=contract.key,
        blueprint=contract,
        source_anchor_ids=contract.source_anchor_ids,
        difficulty=contract.difficulty,
        grader=contract.grader,
        is_core=contract.is_core,
        is_gating=contract.is_gating,
        is_source_level=contract.is_source_level,
    )

    payload = exercise._prepare_save_data()

    assert str(payload["course"]) == "course:one"
    assert str(payload["course_version"]) == "course_version:one"
    assert str(payload["chapter"]) == "chapter:one"
    assert payload["blueprint"]["answer_type"] == "symbolic"


@pytest.mark.asyncio
async def test_task_backend_adapts_existing_command_service_without_leaking_it(
    monkeypatch,
) -> None:
    monkeypatch.setattr(task_backend_module, "repo_query", AsyncMock(return_value=[]))
    service = AsyncMock()
    service.submit_command_job.return_value = "command:one"
    service.get_command_status.return_value = {
        "job_id": "command:one",
        "status": "running",
        "result": None,
        "error_message": None,
        "created": "2026-08-21T00:00:00Z",
        "updated": "2026-08-21T00:01:00Z",
        "progress": {"completed": 1},
    }
    backend = SurrealCommandTaskBackend(command_service=service)
    request = CourseTaskRequest(
        task="learning_recompute",
        idempotency_key="a" * 64,
        arguments=[CourseTaskArgument(name="course_id", value="course:one")],
    )

    job_id = await backend.submit(request)
    status = await backend.get(job_id)
    with pytest.raises(CourseTaskCancellationError, match="running"):
        await backend.cancel(job_id)

    assert job_id == "command:one"
    assert status.job_id == "command:one"
    assert status.status == "running"
    assert status.created == "2026-08-21T00:00:00Z"
    assert status.progress == {"completed": 1}
    service.submit_command_job.assert_awaited_once_with(
        "open_notebook",
        "course_v2_learning_recompute",
        {"course_id": "course:one", "idempotency_key": "a" * 64},
    )
    service.cancel_command_job.assert_not_awaited()
