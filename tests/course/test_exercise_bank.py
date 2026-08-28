from collections.abc import Sequence
from pathlib import Path

import pytest

from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.contracts import (
    CourseOutlineArtifact,
    ModelSelection,
    ValidationFinding,
)
from open_notebook.course.evidence_service import EvidenceInputError, EvidenceService
from open_notebook.course.generation_service import CourseGenerationService
from open_notebook.course.model_adapters import FakeCourseModelAdapter
from open_notebook.course.models import CourseEvidenceAnchor
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    EvidenceClassification,
    ExerciseBankArtifact,
    ExerciseBlueprint,
    NumericGraderSpec,
    TransferDimensionEvidence,
    TransferTaskSpec,
)


def _difficulty(*, reasoning_steps: int = 3) -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=reasoning_steps,
        symbolic_depth=2,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=0,
    )


def _transfer(*, difficulty: DifficultyVector | None = None) -> TransferTaskSpec:
    return TransferTaskSpec(
        key="linear-inverse",
        prompt="Construct an equation whose solution is x = 4.",
        invariant_concept_keys=["linear-equations"],
        dimensions=["inverse_or_constructive"],
        answer_type="numeric",
        change_evidence=[
            TransferDimensionEvidence(
                dimension="inverse_or_constructive",
                source_structure="solve a supplied equation for its unknown",
                target_structure="construct an equation from a supplied solution",
                rationale="The unknown relationship is preserved while the goal is reversed.",
            )
        ],
        difficulty=difficulty or _difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        anchor_ids=[],
    )


def _source_exercise(
    anchor_id: str,
    *,
    key: str = "linear-source",
    reasoning_steps: int = 3,
) -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key=key,
        chapter_key="linear-equations",
        prompt="Exercise 3.2: Solve 2x + 3 = 11.",
        concept_keys=["linear-equations"],
        exercise_type="source_practice",
        answer_type="numeric",
        source_anchor_ids=[anchor_id],
        source_number="3.2",
        difficulty=_difficulty(reasoning_steps=reasoning_steps),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_source_level=True,
    )


def _core(
    anchor_id: str,
    *,
    reasoning_steps: int = 3,
) -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key="linear-core",
        chapter_key="linear-equations",
        prompt="Solve 2x + 3 = 11 and justify each inverse operation.",
        concept_keys=["linear-equations"],
        exercise_type="generated_core",
        answer_type="numeric",
        source_anchor_ids=[anchor_id],
        difficulty=_difficulty(reasoning_steps=reasoning_steps),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        hints=(
            "Identify the invariant relationship.",
            "Represent the relationship with an equation.",
            "Apply one inverse operation at a time.",
            "Check the result by substitution.",
        ),
        is_core=True,
        is_gating=True,
        transfer_task=_transfer(
            difficulty=_difficulty(reasoning_steps=reasoning_steps)
        ),
    )


def _codes(findings: Sequence[ValidationFinding]) -> set[str]:
    return {str(getattr(finding, "item_key")) for finding in findings}


def _outline(anchor_id: str) -> CourseOutlineArtifact:
    return CourseOutlineArtifact.model_validate(
        {
            "title": "Algebra",
            "chapters": [
                {
                    "key": "linear-equations",
                    "title": "Linear equations",
                    "purpose": "Solve linear equations.",
                    "objective_keys": ["linear-equations"],
                    "anchor_ids": [anchor_id],
                    "lab_keys": ["linear-plot"],
                }
            ],
            "concepts": [
                {
                    "key": "linear-equations",
                    "label": "Linear equations",
                    "anchor_ids": [anchor_id],
                }
            ],
        }
    )


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ("Definition 2.1. A vector has magnitude and direction.", "definition"),
        ("Theorem 4. If f is differentiable, then it is continuous.", "theorem"),
        ("Example 3.2. Solve x + 2 = 5. Solution: x = 3.", "worked_example"),
        ("Exercise 7.4. Find the acceleration.", "exercise"),
        ("Answer to Exercise 7.4: 9.8 m/s^2.", "answer"),
        ("Figure 5. Free-body diagram for the block.", "figure"),
        ("Prerequisite review: factoring quadratic expressions.", "prerequisite"),
    ],
)
def test_evidence_quotes_are_classified_for_textbook_assessment(
    quote: str, expected: str
) -> None:
    service = EvidenceService(data_root=Path("/tmp/course-evidence"))
    anchor = service.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256="a" * 64,
        kind="pdf_page",
        index=1,
        block_key="text-1",
        quote=quote,
        source_role="PRIMARY",
    )

    classification = service.classify_assessment_anchor(anchor)

    assert classification.category == expected
    if expected in {"worked_example", "exercise", "answer"}:
        assert classification.source_number is not None


def test_core_exercise_requires_source_anchor_and_textbook_baseline() -> None:
    valid = _core("anchor:source")
    core_without_source = valid.model_copy(
        update={"source_anchor_ids": (), "is_source_level": False}
    )

    findings = AssessmentService().validate_bank([core_without_source])

    assert _codes(findings) == {
        "missing_source_anchor",
        "missing_difficulty_baseline",
    }


def test_core_exercise_requires_exactly_four_authored_hint_layers() -> None:
    core = _core("anchor:source").model_copy(
        update={"hints": ("Identify the invariant.", "Choose a representation.")}
    )

    findings = AssessmentService.validate_bank([core])

    assert "invalid_core_hint_layers" in _codes(findings)


def test_core_must_dominate_confirmed_textbook_baseline() -> None:
    source = _source_exercise("anchor:source", reasoning_steps=5)
    core = _core("anchor:source", reasoning_steps=4)

    findings = AssessmentService().validate_bank(
        [source, core],
        known_anchor_ids={"anchor:source"},
        classifications=[
            EvidenceClassification(
                anchor_id="anchor:source",
                category="exercise",
                confidence="high",
                source_number="3.2",
            )
        ],
    )

    assert "core_below_difficulty_baseline" in _codes(findings)


def test_challenge_requires_a_higher_source_level() -> None:
    source = _source_exercise("anchor:source", reasoning_steps=3)
    core = _core("anchor:source", reasoning_steps=3)
    challenge = ExerciseBlueprint(
        key="linear-challenge",
        chapter_key="linear-equations",
        prompt="Solve a parameterized family of linear equations.",
        concept_keys=["linear-equations"],
        exercise_type="generated_challenge",
        answer_type="numeric",
        difficulty=_difficulty(reasoning_steps=5),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
    )

    findings = AssessmentService().validate_bank([source, core, challenge])

    assert "unconfirmed_challenge_baseline" in _codes(findings)


@pytest.mark.asyncio
async def test_build_exercise_bank_uses_owned_classified_anchors() -> None:
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    anchor = evidence.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256="b" * 64,
        kind="pdf_page",
        index=3,
        block_key="exercise-3-2",
        quote="Exercise 3.2. Solve 2x + 3 = 11.",
        source_role="PRIMARY",
    )
    bank = ExerciseBankArtifact(
        exercises=[_source_exercise(anchor.anchor_id), _core(anchor.anchor_id)]
    )
    adapter = FakeCourseModelAdapter(bank)

    async def load_anchors(
        course_id: str, version_id: str, anchor_ids: tuple[str, ...]
    ) -> tuple[CourseEvidenceAnchor, ...]:
        assert course_id == "course:one"
        assert version_id == "course_version:one"
        assert anchor_ids == (anchor.anchor_id,)
        return (anchor,)

    async def load_outline(course_id: str, version_id: str) -> CourseOutlineArtifact:
        assert course_id == "course:one"
        assert version_id == "course_version:one"
        return _outline(anchor.anchor_id)

    async def review_transfer(
        core: ExerciseBlueprint, transfer: TransferTaskSpec
    ) -> tuple[ValidationFinding, ...]:
        assert core.transfer_task == transfer
        return ()

    service = AssessmentService(
        generation_service=CourseGenerationService(adapter),
        evidence_service=evidence,
        model=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
        ),
        anchor_loader=load_anchors,
        outline_loader=load_outline,
        transfer_reviewer=review_transfer,
    )

    exercises = await service.build_exercise_bank(
        "course:one", "course_version:one", [anchor.anchor_id]
    )

    assert [exercise.key for exercise in exercises] == [
        "linear-source",
        "linear-core",
    ]
    assert adapter.calls[0].request.stage == "exercise_bank"
    assert f"anchor_id={anchor.anchor_id}" in adapter.calls[0].prompt
    assert "category=exercise" in adapter.calls[0].prompt


@pytest.mark.asyncio
async def test_build_exercise_bank_rejects_outline_changed_during_generation() -> None:
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    anchor = evidence.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256="b" * 64,
        kind="pdf_page",
        index=3,
        block_key="exercise-3-2",
        quote="Exercise 3.2. Solve 2x + 3 = 11.",
        source_role="PRIMARY",
    )
    bank = ExerciseBankArtifact(
        exercises=[_source_exercise(anchor.anchor_id), _core(anchor.anchor_id)]
    )
    outline_calls = 0

    async def load_anchors(
        course_id: str, version_id: str, anchor_ids: tuple[str, ...]
    ) -> tuple[CourseEvidenceAnchor, ...]:
        return (anchor,)

    async def load_outline(course_id: str, version_id: str) -> CourseOutlineArtifact:
        nonlocal outline_calls
        outline_calls += 1
        outline = _outline(anchor.anchor_id)
        if outline_calls == 2:
            return outline.model_copy(update={"title": "Changed outline"})
        return outline

    async def review_transfer(
        core: ExerciseBlueprint, transfer: TransferTaskSpec
    ) -> tuple[ValidationFinding, ...]:
        return ()

    service = AssessmentService(
        generation_service=CourseGenerationService(FakeCourseModelAdapter(bank)),
        evidence_service=evidence,
        model=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
        ),
        anchor_loader=load_anchors,
        outline_loader=load_outline,
        transfer_reviewer=review_transfer,
    )

    with pytest.raises(EvidenceInputError, match="changed during generation"):
        await service.build_exercise_bank(
            "course:one", "course_version:one", [anchor.anchor_id]
        )


@pytest.mark.asyncio
async def test_build_exercise_bank_rejects_anchor_changed_during_generation() -> None:
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    anchor = evidence.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256="b" * 64,
        kind="pdf_page",
        index=3,
        block_key="exercise-3-2",
        quote="Exercise 3.2. Solve 2x + 3 = 11.",
        source_role="PRIMARY",
    )
    changed_anchor = anchor.model_copy(
        update={
            "locator": anchor.locator.model_copy(update={"content_sha256": "c" * 64})
        }
    )
    bank = ExerciseBankArtifact(
        exercises=[_source_exercise(anchor.anchor_id), _core(anchor.anchor_id)]
    )
    anchor_calls = 0

    async def load_anchors(
        course_id: str, version_id: str, anchor_ids: tuple[str, ...]
    ) -> tuple[CourseEvidenceAnchor, ...]:
        nonlocal anchor_calls
        anchor_calls += 1
        return (changed_anchor,) if anchor_calls == 2 else (anchor,)

    async def load_outline(course_id: str, version_id: str) -> CourseOutlineArtifact:
        return _outline(anchor.anchor_id)

    async def review_transfer(
        core: ExerciseBlueprint, transfer: TransferTaskSpec
    ) -> tuple[ValidationFinding, ...]:
        return ()

    service = AssessmentService(
        generation_service=CourseGenerationService(FakeCourseModelAdapter(bank)),
        evidence_service=evidence,
        model=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
        ),
        anchor_loader=load_anchors,
        outline_loader=load_outline,
        transfer_reviewer=review_transfer,
    )

    with pytest.raises(EvidenceInputError, match="changed during generation"):
        await service.build_exercise_bank(
            "course:one", "course_version:one", [anchor.anchor_id]
        )


def test_classification_contract_is_strict_and_immutable() -> None:
    classification = EvidenceClassification(
        anchor_id="anchor:one",
        category="exercise",
        confidence="high",
        source_number="3.2",
    )

    assert classification.category == "exercise"
    with pytest.raises(Exception, match="frozen"):
        classification.category = "answer"  # type: ignore[misc]
