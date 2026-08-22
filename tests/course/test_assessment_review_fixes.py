from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.contracts import ModelSelection, ValidationFinding
from open_notebook.course.generation_service import CourseGenerationService
from open_notebook.course.model_adapters import FakeCourseModelAdapter
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
        symbolic_depth=1,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=0,
    )


def _transfer(*, anchor_ids: tuple[str, ...] = ()) -> TransferTaskSpec:
    return TransferTaskSpec(
        key="linear-inverse",
        prompt="Construct an equation whose solution is x = 4.",
        invariant_concept_keys=["linear"],
        dimensions=["inverse_or_constructive"],
        answer_type="numeric",
        change_evidence=[
            TransferDimensionEvidence(
                dimension="inverse_or_constructive",
                source_structure="solve a supplied equation",
                target_structure="construct an equation from its solution",
                rationale="The goal is reversed while the concept remains.",
            )
        ],
        difficulty=_difficulty(3),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        anchor_ids=anchor_ids,
    )


def _core(*, gating: bool = True, chapter_key: str = "linear") -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key=f"{chapter_key}-core",
        chapter_key=chapter_key,
        prompt="Solve the equation and justify each inverse operation.",
        concept_keys=["linear"],
        exercise_type="generated_core",
        answer_type="numeric",
        source_anchor_ids=["anchor:baseline"],
        difficulty=_difficulty(3),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_core=True,
        is_gating=gating,
        transfer_task=_transfer(),
    )


def _source(key: str, anchor_id: str, steps: int, number: str) -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key=key,
        chapter_key="linear",
        prompt=f"Exercise {number}. Solve the equation.",
        concept_keys=["linear"],
        exercise_type="source_practice",
        answer_type="numeric",
        source_anchor_ids=[anchor_id],
        source_number=number,
        difficulty=_difficulty(steps),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_source_level=True,
    )


def _classification(anchor_id: str, number: str) -> EvidenceClassification:
    return EvidenceClassification(
        anchor_id=anchor_id,
        category="exercise",
        confidence="high",
        source_number=number,
    )


def _challenge(steps: int) -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key=f"linear-challenge-{steps}",
        chapter_key="linear",
        prompt="Solve a higher-order parameterized equation.",
        concept_keys=["linear"],
        exercise_type="generated_challenge",
        answer_type="numeric",
        difficulty=_difficulty(steps),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
    )


def _codes(findings: Sequence[ValidationFinding]) -> set[str]:
    return {finding.item_key for finding in findings}


def test_higher_source_tier_supports_a_strictly_harder_challenge() -> None:
    bank = [
        _source("baseline", "anchor:baseline", 3, "3.1"),
        _source("higher", "anchor:higher", 5, "3.9"),
        _core(),
        _challenge(5),
    ]

    findings = AssessmentService.validate_bank(
        bank,
        known_anchor_ids={"anchor:baseline", "anchor:higher"},
        classifications=[
            _classification("anchor:baseline", "3.1"),
            _classification("anchor:higher", "3.9"),
        ],
    )

    assert findings == []


def test_challenge_must_strictly_dominate_the_core() -> None:
    findings = AssessmentService.validate_bank(
        [
            _source("baseline", "anchor:baseline", 3, "3.1"),
            _source("higher", "anchor:higher", 5, "3.9"),
            _core(),
            _challenge(3),
        ],
        known_anchor_ids={"anchor:baseline", "anchor:higher"},
        classifications=[
            _classification("anchor:baseline", "3.1"),
            _classification("anchor:higher", "3.9"),
        ],
    )

    assert "challenge_not_above_core_difficulty" in _codes(findings)


def test_core_exercise_is_always_a_gating_exercise() -> None:
    with pytest.raises(ValidationError, match="core exercise must be gating"):
        _core(gating=False)


def test_model_cannot_invent_a_source_number_for_the_baseline() -> None:
    source = _source("baseline", "anchor:baseline", 3, "invented-99")

    findings = AssessmentService.validate_bank(
        [source, _core()],
        known_anchor_ids={"anchor:baseline"},
        classifications=[_classification("anchor:baseline", "3.1")],
    )

    assert "missing_difficulty_baseline" in _codes(findings)


def test_expected_outline_chapters_must_all_have_a_core() -> None:
    findings = AssessmentService.validate_bank(
        [_source("baseline", "anchor:baseline", 3, "3.1"), _core()],
        known_anchor_ids={"anchor:baseline"},
        classifications=[_classification("anchor:baseline", "3.1")],
        expected_chapter_keys={"linear", "quadratics"},
    )

    assert "missing_chapter_exercises" in _codes(findings)


def test_transfer_review_is_bound_to_its_core_when_keys_collide() -> None:
    first_transfer = _transfer().model_copy(update={"key": "shared-transfer"})
    first_core = _core().model_copy(update={"transfer_task": first_transfer})
    second_source = _source(
        "quadratic-baseline", "anchor:quadratic", 3, "4.1"
    ).model_copy(
        update={"chapter_key": "quadratics", "concept_keys": ("quadratic",)}
    )
    second_transfer = _transfer().model_copy(
        update={
            "key": "shared-transfer",
            "invariant_concept_keys": ("quadratic",),
        }
    )
    second_core = _core(chapter_key="quadratics").model_copy(
        update={
            "concept_keys": ("quadratic",),
            "source_anchor_ids": ("anchor:quadratic",),
            "transfer_task": second_transfer,
        }
    )
    uncertain = ValidationFinding(
        kind="review",
        severity="high",
        status="uncertain",
        item_key="first-transfer-uncertain",
        message="The first transfer could not be established.",
    )

    findings = AssessmentService.validate_bank(
        [
            _source("baseline", "anchor:baseline", 3, "3.1"),
            first_core,
            second_source,
            second_core,
        ],
        known_anchor_ids={"anchor:baseline", "anchor:quadratic"},
        classifications=[
            _classification("anchor:baseline", "3.1"),
            _classification("anchor:quadratic", "4.1"),
        ],
        transfer_reviews={
            first_core.key: [uncertain],
            second_core.key: [],
        },
        require_independent_review=True,
    )

    assert {"duplicate_transfer_key", "first-transfer-uncertain"} <= _codes(
        findings
    )


def test_challenge_cannot_borrow_a_higher_source_from_another_concept() -> None:
    challenge = _challenge(5).model_copy(update={"concept_keys": ("unrelated",)})

    findings = AssessmentService.validate_bank(
        [
            _source("baseline", "anchor:baseline", 3, "3.1"),
            _source("higher", "anchor:higher", 5, "3.9"),
            _core(),
            challenge,
        ],
        known_anchor_ids={"anchor:baseline", "anchor:higher"},
        classifications=[
            _classification("anchor:baseline", "3.1"),
            _classification("anchor:higher", "3.9"),
        ],
    )

    assert "challenge_concept_mismatch" in _codes(findings)


@pytest.mark.asyncio
async def test_standalone_transfer_generation_rejects_unknown_anchor() -> None:
    adapter = FakeCourseModelAdapter(_transfer(anchor_ids=("anchor:foreign",)))
    selected_core = _core().model_copy(
        update={"source_anchor_ids": ("anchor:selected",)}
    )

    with pytest.raises(ValueError, match="unknown evidence anchor"):
        await CourseGenerationService(adapter).generate_transfer_task(
            course_id="course:one",
            chapter_key="linear",
            core=selected_core,
            anchor_ids=["anchor:selected"],
            evidence=["[anchor:selected]: source"],
            model=ModelSelection(
                adapter="codex_cli",
                model="gpt-5.6-sol",
                reasoning_effort="max",
            ),
        )

    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_standalone_transfer_rejects_a_core_with_foreign_anchor() -> None:
    adapter = FakeCourseModelAdapter(_transfer())
    foreign_core = _core().model_copy(
        update={"source_anchor_ids": ("anchor:foreign",)}
    )

    with pytest.raises(ValueError, match="unknown evidence anchor"):
        await CourseGenerationService(adapter).generate_transfer_task(
            course_id="course:one",
            chapter_key="linear",
            core=foreign_core,
            anchor_ids=["anchor:selected"],
            evidence=["[anchor:selected]: source"],
            model=ModelSelection(
                adapter="codex_cli",
                model="gpt-5.6-sol",
                reasoning_effort="max",
            ),
        )

    assert adapter.calls == []
