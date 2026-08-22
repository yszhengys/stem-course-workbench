from collections.abc import Sequence

import pytest

from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.contracts import ValidationFinding
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    ExerciseBlueprint,
    NumericGraderSpec,
    TransferDimension,
    TransferDimensionEvidence,
    TransferTaskSpec,
)


def _difficulty() -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=3,
        symbolic_depth=1,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=1,
    )


def _core() -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key="constant-speed-core",
        chapter_key="constant-speed",
        prompt=(
            "A train travels from Paris to London carrying steel at constant speed. "
            "Calculate travel time."
        ),
        concept_keys=["constant-speed"],
        exercise_type="generated_core",
        answer_type="numeric",
        source_anchor_ids=["anchor:speed"],
        difficulty=_difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="2"),
        is_core=True,
        is_gating=True,
    )


def _transfer() -> TransferTaskSpec:
    return TransferTaskSpec(
        key="constant-speed-context",
        prompt=(
            "A boat sails from Tokyo to Sydney carrying timber at constant speed. "
            "Calculate travel time."
        ),
        invariant_concept_keys=["constant-speed"],
        dimensions=["math_physics_context"],
        answer_type="numeric",
        change_evidence=[
            TransferDimensionEvidence(
                dimension="math_physics_context",
                source_structure="train Paris London steel",
                target_structure="boat Tokyo Sydney timber",
                rationale="The story nouns differ.",
            )
        ],
        difficulty=_difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="2"),
    )


def _codes(findings: Sequence[ValidationFinding]) -> set[str]:
    return {finding.item_key for finding in findings}


def test_multiple_story_noun_swaps_remain_superficial() -> None:
    findings = AssessmentService.validate_transfer(
        _core(), _transfer(), review_findings=[]
    )

    assert "superficial_transfer" in _codes(findings)


@pytest.mark.parametrize(
    ("core_prompt", "transfer_prompt", "source_structure", "target_structure"),
    [
        (
            "A red train carries steel crates from Paris to London at constant speed. Compute its travel time.",
            "A blue submarine transports wooden barrels from Tokyo to Sydney at constant speed. Calculate its journey duration.",
            "red train steel crates Paris London",
            "blue submarine wooden barrels Tokyo Sydney",
        ),
        (
            "一列火车以恒定速度从北京运送钢材到上海，求旅行时间。",
            "一艘轮船以恒定速度从东京运送木材到悉尼，求航行时长。",
            "火车 北京 钢材 上海",
            "轮船 东京 木材 悉尼",
        ),
    ],
    ids=["english", "chinese"],
)
def test_context_noun_and_synonym_swaps_are_not_deep_transfer(
    core_prompt: str,
    transfer_prompt: str,
    source_structure: str,
    target_structure: str,
) -> None:
    core = _core().model_copy(update={"prompt": core_prompt})
    payload = _transfer().model_dump(mode="json")
    payload.update(
        {
            "prompt": transfer_prompt,
            "change_evidence": [
                {
                    "dimension": "math_physics_context",
                    "source_structure": source_structure,
                    "target_structure": target_structure,
                    "rationale": "Only the story setting and nouns change.",
                }
            ],
        }
    )
    transfer = TransferTaskSpec.model_validate(payload)

    findings = AssessmentService.validate_transfer(core, transfer, review_findings=[])

    assert "superficial_transfer" in _codes(findings)


def test_absent_independent_review_fails_closed() -> None:
    findings = AssessmentService.validate_transfer(_core(), _transfer())

    assert "manual_check" in _codes(findings)
    assert any(finding.status == "manual_check" for finding in findings)


@pytest.mark.parametrize(
    ("dimension", "rationale"),
    [
        ("representation", "The problem now uses a new representation."),
        (
            "inverse_or_constructive",
            "The direction is reversed into a constructive task.",
        ),
        (
            "constraints_frame_or_regime",
            "The constraints and regime have changed.",
        ),
        ("method_comparison", "The task now compares two methods."),
        (
            "proof_counterexample_generalization",
            "The task now asks for proof and generalization.",
        ),
    ],
)
def test_declared_dimension_cannot_disguise_a_noun_swap(
    dimension: TransferDimension, rationale: str
) -> None:
    core = _core().model_copy(
        update={
            "prompt": (
                "A red train carries steel crates from Paris to London at "
                "constant speed. Compute its travel time."
            )
        }
    )
    payload = _transfer().model_dump(mode="json")
    payload.update(
        {
            "prompt": (
                "A blue submarine transports wooden barrels from Tokyo to "
                "Sydney at constant speed. Calculate its journey duration."
            ),
            "dimensions": [dimension],
            "change_evidence": [
                {
                    "dimension": dimension,
                    "source_structure": "red train steel crates Paris London",
                    "target_structure": "blue submarine wooden barrels Tokyo Sydney",
                    "rationale": rationale,
                }
            ],
        }
    )
    transfer = TransferTaskSpec.model_validate(payload)

    findings = AssessmentService.validate_transfer(core, transfer, review_findings=[])

    assert "manual_check" in _codes(findings)
