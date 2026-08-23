from collections.abc import Sequence

from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.contracts import ValidationFinding
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    EvidenceClassification,
    ExerciseBlueprint,
    NumericGraderSpec,
    TransferDimensionEvidence,
    TransferTaskSpec,
)


def _difficulty(steps: int) -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=steps,
        symbolic_depth=2,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=0,
    )


def _transfer(*, anchor_ids: tuple[str, ...] = ()) -> TransferTaskSpec:
    return TransferTaskSpec(
        key="linear-inverse",
        prompt="Construct an equation whose solution is x = 4.",
        invariant_concept_keys=["linear-equations"],
        dimensions=["inverse_or_constructive"],
        answer_type="numeric",
        change_evidence=[
            TransferDimensionEvidence(
                dimension="inverse_or_constructive",
                source_structure="solve a supplied equation",
                target_structure="construct an equation from its solution",
                rationale="The goal direction changes while the concept remains.",
            )
        ],
        difficulty=_difficulty(4),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        anchor_ids=anchor_ids,
    )


def _core(*, anchor_id: str = "anchor:source", steps: int = 4) -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key="linear-core",
        chapter_key="linear-equations",
        prompt="Solve 2x + 3 = 11 for x.",
        concept_keys=["linear-equations"],
        exercise_type="generated_core",
        answer_type="numeric",
        source_anchor_ids=[anchor_id],
        difficulty=_difficulty(steps),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        hints=("Identify.", "Represent.", "Solve.", "Check."),
        is_core=True,
        is_gating=True,
        transfer_task=_transfer(),
    )


def _source(*, anchor_id: str = "anchor:source", steps: int = 4) -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key=f"source-{steps}",
        chapter_key="linear-equations",
        prompt=f"Exercise 3.{steps}. Solve the equation.",
        concept_keys=["linear-equations"],
        exercise_type="source_practice",
        answer_type="numeric",
        source_anchor_ids=[anchor_id],
        source_number=f"3.{steps}",
        difficulty=_difficulty(steps),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_source_level=True,
    )


def _classification(anchor_id: str) -> EvidenceClassification:
    return EvidenceClassification(
        anchor_id=anchor_id,
        category="exercise",
        confidence="high",
        source_number="3.4",
    )


def _codes(findings: Sequence[ValidationFinding]) -> set[str]:
    return {finding.item_key for finding in findings}


def test_generated_core_cannot_self_declare_the_textbook_baseline() -> None:
    core = _core().model_copy(
        update={"is_source_level": True, "source_number": "3.4"}
    )

    findings = AssessmentService.validate_bank(
        [core],
        known_anchor_ids={"anchor:source"},
        classifications=[_classification("anchor:source")],
    )

    assert "missing_difficulty_baseline" in _codes(findings)


def test_every_exercise_and_nested_transfer_anchor_must_be_selected() -> None:
    source = _source(anchor_id="anchor:unknown")
    core = _core().model_copy(
        update={"transfer_task": _transfer(anchor_ids=("anchor:unknown",))}
    )

    findings = AssessmentService.validate_bank(
        [source, core],
        known_anchor_ids={"anchor:source"},
        classifications=[_classification("anchor:unknown")],
    )

    assert "unknown_source_anchor" in _codes(findings)
    assert "unknown_transfer_anchor" in _codes(findings)


def test_challenge_must_not_be_easier_than_the_core() -> None:
    source = _source(steps=5)
    core = _core(steps=4)
    challenge = ExerciseBlueprint(
        key="linear-challenge",
        chapter_key="linear-equations",
        prompt="Solve a challenge equation.",
        concept_keys=["linear-equations"],
        exercise_type="generated_challenge",
        answer_type="numeric",
        difficulty=_difficulty(3),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
    )

    findings = AssessmentService.validate_bank([source, core, challenge])

    assert "challenge_below_core_difficulty" in _codes(findings)
