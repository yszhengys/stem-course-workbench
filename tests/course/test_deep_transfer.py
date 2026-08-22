from collections.abc import Sequence

import pytest

from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.contracts import ValidationFinding
from open_notebook.course.v2_contracts import (
    AdvisoryGraderSpec,
    AnswerType,
    DifficultyVector,
    ExerciseBlueprint,
    GraderSpec,
    NumericGraderSpec,
    SetGraderSpec,
    SymbolicGraderSpec,
    TransferDimension,
    TransferDimensionEvidence,
    TransferTaskSpec,
    UnitGraderSpec,
)


def _difficulty(*, reasoning_steps: int = 4) -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=reasoning_steps,
        symbolic_depth=2,
        representation_shifts=1,
        proof_burden=0,
        physics_constraints=0,
    )


def _core(prompt: str = "Solve 2x + 3 = 11 for x.") -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key="linear-core",
        chapter_key="linear-equations",
        prompt=prompt,
        concept_keys=["linear-equations"],
        exercise_type="generated_core",
        answer_type="numeric",
        source_anchor_ids=["anchor:linear"],
        difficulty=_difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_core=True,
        is_gating=True,
    )


def _answer_contract(dimension: TransferDimension) -> tuple[AnswerType, GraderSpec]:
    if dimension == "inverse_or_constructive":
        return "symbolic", SymbolicGraderSpec(
            kind="symbolic",
            expected_expression="2*x - 8",
            allowed_symbols=["x"],
        )
    if dimension == "constraints_frame_or_regime":
        return "set", SetGraderSpec(kind="set", expected_items=["no-solution-regime"])
    if dimension == "method_comparison":
        return "explanation", AdvisoryGraderSpec(
            kind="advisory", rubric="Compare both valid methods and assumptions."
        )
    if dimension == "proof_counterexample_generalization":
        return "proof", AdvisoryGraderSpec(
            kind="advisory", rubric="Check the proof and boundary counterexample."
        )
    if dimension == "math_physics_context":
        return "unit", UnitGraderSpec(
            kind="unit", expected_value="2", expected_unit="m/s^2"
        )
    return "numeric", NumericGraderSpec(kind="numeric", expected="4")


def _transfer(
    dimension: TransferDimension,
    *,
    prompt: str,
    source_structure: str,
    target_structure: str,
    evidence: bool = True,
    difficulty: DifficultyVector | None = None,
    concept_keys: tuple[str, ...] = ("linear-equations",),
) -> TransferTaskSpec:
    answer_type, grader = _answer_contract(dimension)
    return TransferTaskSpec(
        key=f"linear-{dimension}",
        prompt=prompt,
        invariant_concept_keys=concept_keys,
        dimensions=[dimension],
        answer_type=answer_type,
        change_evidence=(
            [
                TransferDimensionEvidence(
                    dimension=dimension,
                    source_structure=source_structure,
                    target_structure=target_structure,
                    rationale="The invariant equation relationship is retained across the structural change.",
                )
            ]
            if evidence
            else []
        ),
        difficulty=difficulty or _difficulty(),
        grader=grader,
    )


def _codes(findings: Sequence[ValidationFinding]) -> set[str]:
    return {str(getattr(finding, "item_key")) for finding in findings}


@pytest.mark.parametrize(
    ("dimension", "prompt", "source_structure", "target_structure"),
    [
        (
            "representation",
            "Read the intersection from a graph and verify it algebraically.",
            "symbolic equation",
            "graphical intersection",
        ),
        (
            "inverse_or_constructive",
            "Construct a linear equation whose solution is x = 4.",
            "solve a supplied equation",
            "construct an equation from its solution",
        ),
        (
            "constraints_frame_or_regime",
            "Determine when the parameterized equation has no solution.",
            "one fixed solvable equation",
            "parameter regime with solvability constraints",
        ),
        (
            "method_comparison",
            "Solve by elimination and substitution, then compare both methods.",
            "one inverse-operation method",
            "two methods with an explicit comparison",
        ),
        (
            "proof_counterexample_generalization",
            "Prove the uniqueness rule and give a counterexample when its premise fails.",
            "calculate one solution",
            "prove a general rule and test its boundary",
        ),
        (
            "math_physics_context",
            "Infer a cart's constant acceleration from a linear velocity-time relation.",
            "abstract linear relation",
            "physics relation with quantities and constraints",
        ),
    ],
)
def test_six_deep_transfer_families_are_accepted(
    dimension: TransferDimension,
    prompt: str,
    source_structure: str,
    target_structure: str,
) -> None:
    findings = AssessmentService().validate_transfer(
        _core(),
        _transfer(
            dimension,
            prompt=prompt,
            source_structure=source_structure,
            target_structure=target_structure,
        ),
        review_findings=[],
    )

    assert findings == []


@pytest.mark.parametrize(
    ("core_prompt", "transfer_prompt"),
    [
        ("Solve 2x + 3 = 11 for x.", "Solve 4x + 3 = 19 for x."),
        ("Solve 2x + 3 = 11 for x.", "Solve 2y + 3 = 11 for y."),
        (
            "A train travels 120 km in 2 hours. Find its speed.",
            "A car travels 120 km in 2 hours. Find its speed.",
        ),
    ],
    ids=["numbers_only", "symbol_rename", "noun_swap"],
)
def test_superficial_transfer_is_rejected(
    core_prompt: str, transfer_prompt: str
) -> None:
    core = _core(core_prompt)
    transfer = _transfer(
        "representation",
        prompt=transfer_prompt,
        source_structure=core_prompt,
        target_structure=transfer_prompt,
        evidence=False,
    )

    assert "superficial_transfer" in _codes(
        AssessmentService().validate_transfer(core, transfer)
    )


def test_transfer_difficulty_cannot_drop_below_core() -> None:
    transfer = _transfer(
        "inverse_or_constructive",
        prompt="Construct an equation whose solution is x = 4.",
        source_structure="solve a supplied equation",
        target_structure="construct an equation from its solution",
        difficulty=_difficulty(reasoning_steps=3),
    )

    assert "transfer_below_core_difficulty" in _codes(
        AssessmentService().validate_transfer(_core(), transfer)
    )


def test_transfer_must_preserve_the_core_concepts() -> None:
    transfer = _transfer(
        "representation",
        prompt="Read a quadratic root from its graph.",
        source_structure="linear equation",
        target_structure="quadratic graph",
        concept_keys=("quadratic-equations",),
    )

    assert "concept_invariant_mismatch" in _codes(
        AssessmentService().validate_transfer(_core(), transfer)
    )


def test_unproven_deep_change_fails_closed_for_manual_review() -> None:
    transfer = _transfer(
        "method_comparison",
        prompt="Use two different methods to solve and compare their assumptions.",
        source_structure="one method",
        target_structure="two methods",
        evidence=False,
    )

    findings = AssessmentService().validate_transfer(_core(), transfer)

    assert _codes(findings) == {"manual_check"}
    assert findings[0].status == "manual_check"


def test_unrecognized_but_plausible_generalization_requires_manual_review() -> None:
    transfer = _transfer(
        "proof_counterexample_generalization",
        prompt="Establish P(n) for every natural n.",
        source_structure="P(4) holds",
        target_structure="P(n) holds for every natural n",
    )

    findings = AssessmentService.validate_transfer(
        _core("For n=4, verify P(n)."), transfer, review_findings=[]
    )

    assert _codes(findings) == {"manual_check"}
    assert findings[0].status == "manual_check"
