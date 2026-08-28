from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from open_notebook.course.authoring_service import DraftScope
from open_notebook.course.publication_service import (
    ExercisePublicationError,
    PublicationService,
)
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    ExerciseBlueprint,
    ExerciseVerification,
    NumericGraderSpec,
)
from open_notebook.course.v2_models import CourseExercise


def _scope() -> DraftScope:
    return DraftScope(
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_id="chapter:one",
        chapter_key="linear",
        chapter_status="ready",
        version_status="generating",
        allowed_anchor_ids=("anchor:linear",),
    )


def _difficulty() -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=2,
        symbolic_depth=1,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=0,
    )


def _exercise(
    key: str = "linear-core",
    *,
    core: bool = True,
    gating: bool = True,
    verified: bool = True,
    transfer: bool = True,
    chapter: str = "chapter:one",
) -> CourseExercise:
    blueprint = ExerciseBlueprint(
        key=key,
        chapter_key="linear",
        prompt="Solve the equation.",
        concept_keys=("linear-equations",),
        exercise_type="generated_core" if core else "source_practice",
        answer_type="numeric",
        source_anchor_ids=("anchor:linear",),
        difficulty=_difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_core=core,
        is_gating=gating,
        is_source_level=not core,
        transfer_task=(
            {
                "key": f"{key}-transfer",
                "prompt": "Apply the equation in a changed representation.",
                "invariant_concept_keys": ["linear-equations"],
                "dimensions": ["representation"],
                "answer_type": "numeric",
                "difficulty": _difficulty().model_dump(mode="json"),
                "grader": {"kind": "numeric", "expected": "8"},
                "anchor_ids": ["anchor:linear"],
            }
            if transfer
            else None
        ),
    )
    verification = (
        ExerciseVerification(
            level="L2",
            method="deterministic_solver",
            reason="SymPy exact substitution transcript sha256:abc",
        )
        if verified
        else ExerciseVerification(
            level="L1", method="independent_model_review"
        )
    )
    return CourseExercise(
        id=f"course_exercise:{key}",
        course="course:one",
        course_version="course_version:one",
        chapter=chapter,
        chapter_key="linear",
        exercise_key=key,
        blueprint=blueprint,
        source_anchor_ids=blueprint.source_anchor_ids,
        difficulty=blueprint.difficulty,
        grader=blueprint.grader,
        is_core=core,
        is_gating=gating,
        is_source_level=not core,
        verification=verification,
    )


def _rows(*exercises: CourseExercise) -> list[dict[str, object]]:
    return [exercise.model_dump(mode="json") for exercise in exercises]


@pytest.mark.asyncio
async def test_verified_objective_core_with_transfer_is_publishable() -> None:
    query = AsyncMock(return_value=_rows(_exercise()))

    await PublicationService(revision_query=query).assert_exercises_ready(_scope())

    call = query.await_args
    assert call is not None
    params = call.args[1]
    assert str(params["course"]) == "course:one"
    assert str(params["version"]) == "course_version:one"
    assert params["chapter_key"] == "linear"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("records", "message"),
    [
        ((), "exercise bank"),
        ((_exercise(core=False, gating=False, transfer=False),), "one core"),
        (
            (_exercise(), _exercise("linear-core-2")),
            "one core",
        ),
        ((_exercise(verified=False),), "L2 or L3"),
        ((_exercise(transfer=False),), "transfer"),
        ((_exercise(chapter="chapter:old"),), "stale chapter"),
    ],
)
async def test_publication_fails_closed_for_incomplete_or_stale_exercise_bank(
    records: tuple[CourseExercise, ...], message: str
) -> None:
    query = AsyncMock(return_value=_rows(*records))

    with pytest.raises(ExercisePublicationError, match=message):
        await PublicationService(revision_query=query).assert_exercises_ready(
            _scope()
        )
