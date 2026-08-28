import pytest

from open_notebook.course.contracts import (
    ModelSelection,
    ReviewArtifact,
    ValidationFinding,
)
from open_notebook.course.generation_service import CourseGenerationService
from open_notebook.course.model_adapters import FakeCourseModelAdapter
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    ExerciseBlueprint,
    NumericGraderSpec,
    TransferDimensionEvidence,
    TransferTaskSpec,
)


def _core(anchor_id: str) -> ExerciseBlueprint:
    difficulty = DifficultyVector(
        concept_count=1,
        reasoning_steps=3,
        symbolic_depth=2,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=0,
    )
    transfer = TransferTaskSpec(
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
                rationale="The relationship is preserved while the goal is reversed.",
            )
        ],
        difficulty=difficulty,
        grader=NumericGraderSpec(kind="numeric", expected="4"),
    )
    return ExerciseBlueprint(
        key="linear-core",
        chapter_key="linear-equations",
        prompt="Solve 2x + 3 = 11 and justify each inverse operation.",
        concept_keys=["linear-equations"],
        exercise_type="generated_core",
        answer_type="numeric",
        source_anchor_ids=[anchor_id],
        difficulty=difficulty,
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        hints=("Identify.", "Represent.", "Solve.", "Check."),
        is_core=True,
        is_gating=True,
        transfer_task=transfer,
    )


@pytest.mark.asyncio
async def test_exercise_review_uses_dedicated_stage_and_minimal_context() -> None:
    core = _core("anchor:required")
    output = ReviewArtifact(
        findings=[
            ValidationFinding(
                kind="review",
                severity="info",
                status="resolved",
                item_key=core.key,
                anchor_ids=["anchor:required"],
                message="The transfer preserves the invariant and changes the goal.",
            )
        ]
    )
    adapter = FakeCourseModelAdapter(output)
    service = CourseGenerationService(adapter)

    findings = await service.review_exercise_transfer(
        course_id="course:one",
        chapter_key="linear-equations",
        core=core,
        evidence_by_anchor={
            "anchor:required": "Exercise 3.2. Solve 2x + 3 = 11.",
            "anchor:unrelated": "The quadratic formula is unrelated context.",
        },
        model=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-luna", reasoning_effort="max"
        ),
    )

    assert findings == tuple(output.findings)
    call = adapter.calls[0]
    assert call.request.stage == "exercise_bank_review"
    assert call.request.chapter_key == "linear-equations"
    assert call.request.anchor_ids == ["anchor:required"]
    assert "Exercise 3.2. Solve 2x + 3 = 11." in call.prompt
    assert "quadratic formula" not in call.prompt
    assert core.prompt in call.prompt
    assert core.transfer_task is not None
    assert core.transfer_task.prompt in call.prompt
    assert "do not regenerate" in call.prompt.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finding",
    [
        ValidationFinding(
            kind="review",
            severity="high",
            item_key="another-core",
            message="Wrong item binding.",
        ),
        ValidationFinding(
            kind="citation",
            severity="high",
            item_key="linear-core",
            message="Wrong finding kind.",
        ),
        ValidationFinding(
            kind="review",
            severity="high",
            item_key="linear-core",
            anchor_ids=["anchor:unknown"],
            message="Unknown evidence binding.",
        ),
    ],
)
async def test_exercise_review_rejects_unbound_findings(
    finding: ValidationFinding,
) -> None:
    core = _core("anchor:required")
    service = CourseGenerationService(
        FakeCourseModelAdapter(ReviewArtifact(findings=[finding]))
    )

    with pytest.raises(ValueError, match="review finding"):
        await service.review_exercise_transfer(
            course_id="course:one",
            chapter_key="linear-equations",
            core=core,
            evidence_by_anchor={"anchor:required": "Required quote."},
            model=ModelSelection(adapter="ollama", model="gpt-oss:20b"),
        )
