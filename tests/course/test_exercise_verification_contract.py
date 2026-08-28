from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from open_notebook.course.v2_contracts import (
    DifficultyVector,
    ExerciseBlueprint,
    ExerciseVerification,
    NumericGraderSpec,
)
from open_notebook.course.v2_models import CourseExercise


def _difficulty() -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=2,
        symbolic_depth=1,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=0,
    )


def _blueprint() -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key="motion-core",
        chapter_key="motion",
        prompt="Find the acceleration.",
        concept_keys=("acceleration",),
        exercise_type="generated_core",
        answer_type="numeric",
        source_anchor_ids=("anchor:motion",),
        difficulty=_difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="9.8"),
        is_core=True,
        is_gating=True,
    )


def test_verification_levels_expose_mastery_eligibility_without_client_flag() -> None:
    verified_at = datetime(2026, 8, 28, tzinfo=timezone.utc)
    cases = (
        (
            ExerciseVerification(level="L0", method="structure"),
            False,
        ),
        (
            ExerciseVerification(level="L1", method="independent_model_review"),
            False,
        ),
        (
            ExerciseVerification(
                level="L2",
                method="source_answer",
                anchor_ids=("anchor:answer",),
            ),
            True,
        ),
        (
            ExerciseVerification(
                level="L2",
                method="deterministic_solver",
                reason="SymPy exact solve transcript sha256:abc",
            ),
            True,
        ),
        (
            ExerciseVerification(
                level="L3",
                method="human_review",
                reason="Teacher checked the displayed expected answer.",
                verified_at=verified_at,
            ),
            True,
        ),
    )

    for verification, expected in cases:
        assert verification.mastery_eligible is expected
        assert "mastery_eligible" not in verification.model_dump()


@pytest.mark.parametrize(
    "payload",
    [
        {"level": "L0", "method": "self_consistency"},
        {
            "level": "L0",
            "method": "structure",
            "anchor_ids": ["anchor:one"],
        },
        {
            "level": "L0",
            "method": "structure",
            "verified_at": "2026-08-28T00:00:00Z",
        },
        {"level": "L1", "method": "structure"},
        {
            "level": "L1",
            "method": "independent_model_review",
            "verified_at": "2026-08-28T00:00:00Z",
        },
        {"level": "L2", "method": "source_answer"},
        {"level": "L2", "method": "deterministic_solver"},
        {
            "level": "L2",
            "method": "independent_model_review",
            "anchor_ids": ["anchor:answer"],
        },
        {
            "level": "L3",
            "method": "human_review",
            "reason": "Teacher checked it.",
        },
        {
            "level": "L3",
            "method": "human_review",
            "verified_at": "2026-08-28T00:00:00Z",
        },
        {
            "level": "L3",
            "method": "source_answer",
            "reason": "Teacher checked it.",
            "verified_at": "2026-08-28T00:00:00Z",
        },
        {
            "level": "L2",
            "method": "source_answer",
            "anchor_ids": ["anchor:answer", "anchor:answer"],
        },
    ],
)
def test_invalid_verification_level_provenance_combinations_fail_closed(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ExerciseVerification.model_validate(payload)


def test_course_exercise_defaults_legacy_records_to_l1_and_persists_run_provenance() -> None:
    blueprint = _blueprint()
    legacy = CourseExercise(
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
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
    assert legacy.verification == ExerciseVerification(
        level="L1", method="self_consistency"
    )
    assert legacy.verification.mastery_eligible is False
    assert legacy.generation_run is None
    assert legacy.review_run_ids == ()

    reviewed = legacy.model_copy(
        update={
            "verification": ExerciseVerification(
                level="L1",
                method="independent_model_review",
                anchor_ids=("anchor:motion",),
            ),
            "generation_run": "course_generation_run:generation",
            "review_run_ids": ("course_generation_run:review",),
        }
    )
    payload = reviewed._prepare_save_data()

    assert payload["verification"]["level"] == "L1"
    assert str(payload["generation_run"]) == "course_generation_run:generation"
    assert [str(value) for value in payload["review_run_ids"]] == [
        "course_generation_run:review"
    ]
