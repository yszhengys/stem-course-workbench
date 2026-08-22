from pathlib import Path

import pytest

from open_notebook.course.assessment_service import AssessmentService, dominates
from open_notebook.course.contracts import ModelSelection, ValidationFinding
from open_notebook.course.evidence_service import EvidenceInputError, EvidenceService
from open_notebook.course.models import CourseEvidenceAnchor
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    ExerciseBlueprint,
    NumericGraderSpec,
    TransferDimensionEvidence,
    TransferTaskSpec,
)


def _difficulty(reasoning_steps: int) -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=reasoning_steps,
        symbolic_depth=2,
        representation_shifts=1,
        proof_burden=0,
        physics_constraints=0,
    )


def _core() -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key="linear-core",
        chapter_key="linear-equations",
        prompt="Solve 2x + 3 = 11 for x.",
        concept_keys=["linear-equations"],
        exercise_type="generated_core",
        answer_type="numeric",
        source_anchor_ids=["anchor:linear"],
        difficulty=_difficulty(4),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_core=True,
        is_gating=True,
    )


def _valid_transfer() -> TransferTaskSpec:
    return TransferTaskSpec(
        key="linear-construction",
        prompt="Construct an equation whose unique solution is x = 4.",
        invariant_concept_keys=["linear-equations"],
        dimensions=["inverse_or_constructive"],
        answer_type="numeric",
        change_evidence=[
            TransferDimensionEvidence(
                dimension="inverse_or_constructive",
                source_structure="solve a supplied equation",
                target_structure="construct an equation from its solution",
                rationale="The goal direction changes while the equation concept remains.",
            )
        ],
        difficulty=_difficulty(4),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
    )


def test_difficulty_dominance_is_componentwise() -> None:
    assert dominates(_difficulty(5), _difficulty(4)) is True
    candidate = _difficulty(5).model_copy(update={"symbolic_depth": 1})
    assert dominates(candidate, _difficulty(4)) is False


def test_uncertain_independent_review_requires_manual_check() -> None:
    review = ValidationFinding(
        kind="review",
        severity="high",
        status="uncertain",
        item_key="review-transfer",
        message="The independent reviewer could not establish equivalence.",
    )

    findings = AssessmentService.validate_transfer(
        _core(), _valid_transfer(), review_findings=[review]
    )

    assert any(
        finding.item_key == "manual_check" and finding.status == "manual_check"
        for finding in findings
    )


@pytest.mark.asyncio
async def test_build_bank_rejects_cross_course_anchor_before_generation() -> None:
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    anchor = evidence.make_anchor(
        course_id="course:other",
        source_id="source:one",
        source_sha256="c" * 64,
        kind="pdf_page",
        index=1,
        block_key="exercise",
        quote="Exercise 1. Solve x = 1.",
        source_role="PRIMARY",
    )

    async def load_anchors(
        course_id: str, version_id: str, anchor_ids: tuple[str, ...]
    ) -> tuple[CourseEvidenceAnchor, ...]:
        return (anchor,)

    service = AssessmentService(
        evidence_service=evidence,
        model=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
        ),
        anchor_loader=load_anchors,
    )

    with pytest.raises(EvidenceInputError, match="owned"):
        await service.build_exercise_bank(
            "course:one", "course_version:one", [anchor.anchor_id]
        )
