from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel

from open_notebook.course.assessment_service import (
    AssessmentService,
    AssessmentValidationError,
)
from open_notebook.course.contracts import (
    CourseOutlineArtifact,
    GenerationRequest,
    ModelSelection,
    ReviewArtifact,
    ValidationFinding,
)
from open_notebook.course.evidence_service import EvidenceInputError, EvidenceService
from open_notebook.course.generation_service import CourseGenerationService
from open_notebook.course.model_adapters import CourseModelAdapter
from open_notebook.course.models import Chapter, CourseEvidenceAnchor
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    ExerciseBankArtifact,
    ExerciseBlueprint,
    NumericGraderSpec,
    TransferDimensionEvidence,
    TransferTaskSpec,
)


class SequenceAdapter(CourseModelAdapter):
    def __init__(self, outputs: Sequence[BaseModel]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple[GenerationRequest, type[BaseModel], str]] = []

    async def generate(
        self,
        request: GenerationRequest,
        output_model: type[BaseModel],
        *,
        prompt: str,
    ) -> BaseModel:
        self.calls.append((request, output_model, prompt))
        output = self.outputs.pop(0)
        return output_model.model_validate(output.model_dump(mode="json"))


def _difficulty(steps: int = 3) -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=steps,
        symbolic_depth=2,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=0,
    )


def _transfer() -> TransferTaskSpec:
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
                rationale="The relationship is preserved while the goal is reversed.",
            )
        ],
        difficulty=_difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
    )


def _source(anchor_id: str) -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key="linear-source",
        chapter_key="linear-equations",
        prompt="Exercise 3.2. Solve 2x + 3 = 11.",
        concept_keys=["linear-equations"],
        exercise_type="source_practice",
        answer_type="numeric",
        source_anchor_ids=[anchor_id],
        source_number="3.2",
        difficulty=_difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_source_level=True,
    )


def _core(anchor_id: str) -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key="linear-core",
        chapter_key="linear-equations",
        prompt="Solve 2x + 3 = 11 and justify each inverse operation.",
        concept_keys=["linear-equations"],
        exercise_type="generated_core",
        answer_type="numeric",
        source_anchor_ids=[anchor_id],
        difficulty=_difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        hints=("Identify.", "Represent.", "Solve.", "Check."),
        is_core=True,
        is_gating=True,
        transfer_task=_transfer(),
    )


def _outline(target_anchor: str, other_anchor: str) -> CourseOutlineArtifact:
    return CourseOutlineArtifact.model_validate(
        {
            "title": "Algebra",
            "chapters": [
                {
                    "key": "linear-equations",
                    "title": "Linear equations",
                    "purpose": "Solve linear equations.",
                    "objective_keys": ["linear-equations"],
                    "anchor_ids": [target_anchor],
                    "lab_keys": ["linear-plot"],
                },
                {
                    "key": "quadratics",
                    "title": "Quadratics",
                    "purpose": "Solve quadratic equations.",
                    "objective_keys": ["quadratics"],
                    "anchor_ids": [other_anchor],
                    "lab_keys": ["quadratic-plot"],
                },
            ],
            "concepts": [
                {
                    "key": "linear-equations",
                    "label": "Linear equations",
                    "anchor_ids": [target_anchor],
                },
                {
                    "key": "quadratics",
                    "label": "Quadratics",
                    "anchor_ids": [other_anchor],
                },
            ],
        }
    )


def _chapter() -> Chapter:
    return Chapter(
        id="chapter:linear",
        course_version="course_version:one",
        chapter_no=1,
        title="Linear equations",
        chapter_key="linear-equations",
        version_no=2,
        input_hash="chapter-input-hash",
        artifact={"chapter_key": "linear-equations", "content": "current"},
        status="ready",
    )


def _anchor(evidence: EvidenceService, *, block: str, quote: str) -> CourseEvidenceAnchor:
    return evidence.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256="b" * 64,
        kind="pdf_page",
        index=3,
        block_key=block,
        quote=quote,
        source_role="PRIMARY",
    )


@pytest.mark.asyncio
async def test_chapter_build_scopes_generation_and_independent_review() -> None:
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    target = _anchor(
        evidence,
        block="exercise-3-2",
        quote="Exercise 3.2. Solve 2x + 3 = 11.",
    )
    other = _anchor(
        evidence,
        block="exercise-4-1",
        quote="Exercise 4.1. Factor x squared minus four.",
    )
    adapter = SequenceAdapter(
        [
            ExerciseBankArtifact(
                exercises=[_source(target.anchor_id), _core(target.anchor_id)]
            ),
            ReviewArtifact(findings=[]),
        ]
    )

    async def load_anchors(
        course_id: str, version_id: str, anchor_ids: tuple[str, ...]
    ) -> tuple[CourseEvidenceAnchor, ...]:
        assert (course_id, version_id, anchor_ids) == (
            "course:one",
            "course_version:one",
            (target.anchor_id,),
        )
        return (target,)

    async def load_outline(
        course_id: str, version_id: str
    ) -> CourseOutlineArtifact:
        return _outline(target.anchor_id, other.anchor_id)

    async def load_chapter(version_id: str, chapter_key: str) -> Chapter:
        assert (version_id, chapter_key) == (
            "course_version:one",
            "linear-equations",
        )
        return _chapter()

    service = AssessmentService(
        generation_service=CourseGenerationService(adapter),
        evidence_service=evidence,
        model=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
        ),
        review_model=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-luna", reasoning_effort="max"
        ),
        anchor_loader=load_anchors,
        outline_loader=load_outline,
        chapter_loader=load_chapter,
    )

    exercises = await service.build_chapter_exercise_bank(
        "course:one",
        "course_version:one",
        "linear-equations",
        [target.anchor_id],
    )

    assert [exercise.key for exercise in exercises] == [
        "linear-source",
        "linear-core",
    ]
    assert [call[0].stage for call in adapter.calls] == [
        "exercise_bank",
        "exercise_bank_review",
    ]
    generation_prompt = adapter.calls[0][2]
    assert "linear-equations" in generation_prompt
    assert "quadratics" not in generation_prompt
    assert other.locator.quote not in generation_prompt
    review_prompt = adapter.calls[1][2]
    assert target.locator.quote in review_prompt
    assert other.locator.quote not in review_prompt


@pytest.mark.asyncio
async def test_chapter_build_rejects_anchor_outside_target_outline_chapter() -> None:
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    target = _anchor(evidence, block="linear", quote="Exercise 3.2. Solve x = 4.")
    other = _anchor(evidence, block="quadratic", quote="Exercise 4.1. Factor x² − 4.")
    adapter = SequenceAdapter([])

    async def load_anchors(
        course_id: str, version_id: str, anchor_ids: tuple[str, ...]
    ) -> tuple[CourseEvidenceAnchor, ...]:
        return (other,)

    async def load_outline(
        course_id: str, version_id: str
    ) -> CourseOutlineArtifact:
        return _outline(target.anchor_id, other.anchor_id)

    async def load_chapter(version_id: str, chapter_key: str) -> Chapter:
        return _chapter()

    service = AssessmentService(
        generation_service=CourseGenerationService(adapter),
        evidence_service=evidence,
        model=ModelSelection(adapter="ollama", model="qwen3.5:9b"),
        review_model=ModelSelection(adapter="ollama", model="qwen3.5:9b"),
        anchor_loader=load_anchors,
        outline_loader=load_outline,
        chapter_loader=load_chapter,
    )

    with pytest.raises(EvidenceInputError, match="target outline chapter"):
        await service.build_chapter_exercise_bank(
            "course:one",
            "course_version:one",
            "linear-equations",
            [other.anchor_id],
        )

    assert adapter.calls == []


@pytest.mark.asyncio
async def test_uncertain_independent_review_blocks_chapter_bank() -> None:
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    target = _anchor(
        evidence,
        block="exercise-3-2",
        quote="Exercise 3.2. Solve 2x + 3 = 11.",
    )
    other = _anchor(evidence, block="other", quote="Exercise 4.1. Factor x² − 4.")
    adapter = SequenceAdapter(
        [
            ExerciseBankArtifact(
                exercises=[_source(target.anchor_id), _core(target.anchor_id)]
            ),
            ReviewArtifact(
                findings=[
                    ValidationFinding(
                        kind="review",
                        severity="high",
                        status="uncertain",
                        item_key="linear-core",
                        anchor_ids=[target.anchor_id],
                        message="The structural change cannot be established.",
                    )
                ]
            ),
        ]
    )

    async def load_anchors(
        course_id: str, version_id: str, anchor_ids: tuple[str, ...]
    ) -> tuple[CourseEvidenceAnchor, ...]:
        return (target,)

    async def load_outline(
        course_id: str, version_id: str
    ) -> CourseOutlineArtifact:
        return _outline(target.anchor_id, other.anchor_id)

    async def load_chapter(version_id: str, chapter_key: str) -> Chapter:
        return _chapter()

    service = AssessmentService(
        generation_service=CourseGenerationService(adapter),
        evidence_service=evidence,
        model=ModelSelection(adapter="ollama", model="qwen3.5:9b"),
        review_model=ModelSelection(adapter="ollama", model="gpt-oss:20b"),
        anchor_loader=load_anchors,
        outline_loader=load_outline,
        chapter_loader=load_chapter,
    )

    with pytest.raises(AssessmentValidationError) as exc_info:
        await service.build_chapter_exercise_bank(
            "course:one",
            "course_version:one",
            "linear-equations",
            [target.anchor_id],
        )

    assert {finding.item_key for finding in exc_info.value.findings} >= {
        "linear-core",
        "manual_check",
    }


@pytest.mark.asyncio
async def test_chapter_input_snapshot_detects_current_chapter_change() -> None:
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    target = _anchor(
        evidence,
        block="exercise-3-2",
        quote="Exercise 3.2. Solve 2x + 3 = 11.",
    )
    other = _anchor(evidence, block="other", quote="Exercise 4.1. Factor x² − 4.")
    adapter = SequenceAdapter(
        [
            ExerciseBankArtifact(
                exercises=[_source(target.anchor_id), _core(target.anchor_id)]
            ),
            ReviewArtifact(findings=[]),
        ]
    )
    chapter_calls = 0

    async def load_anchors(
        course_id: str, version_id: str, anchor_ids: tuple[str, ...]
    ) -> tuple[CourseEvidenceAnchor, ...]:
        return (target,)

    async def load_outline(
        course_id: str, version_id: str
    ) -> CourseOutlineArtifact:
        return _outline(target.anchor_id, other.anchor_id)

    async def load_chapter(version_id: str, chapter_key: str) -> Chapter:
        nonlocal chapter_calls
        chapter_calls += 1
        chapter = _chapter()
        if chapter_calls == 2:
            return chapter.model_copy(update={"input_hash": "changed"})
        return chapter

    service = AssessmentService(
        generation_service=CourseGenerationService(adapter),
        evidence_service=evidence,
        model=ModelSelection(adapter="ollama", model="qwen3.5:9b"),
        review_model=ModelSelection(adapter="ollama", model="gpt-oss:20b"),
        anchor_loader=load_anchors,
        outline_loader=load_outline,
        chapter_loader=load_chapter,
    )

    with pytest.raises(EvidenceInputError, match="changed during generation"):
        await service.build_chapter_exercise_bank(
            "course:one",
            "course_version:one",
            "linear-equations",
            [target.anchor_id],
        )
