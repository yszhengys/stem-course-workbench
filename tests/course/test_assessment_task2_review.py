from collections.abc import Sequence
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.contracts import (
    CourseOutlineArtifact,
    ModelSelection,
    ValidationFinding,
)
from open_notebook.course.evidence_service import EvidenceInputError, EvidenceService
from open_notebook.course.generation_service import CourseGenerationService
from open_notebook.course.model_adapters import FakeCourseModelAdapter
from open_notebook.course.models import Course, CourseVersion
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    EvidenceClassification,
    ExerciseBankArtifact,
    ExerciseBlueprint,
    NumericGraderSpec,
    SymbolicGraderSpec,
    TransferDimensionEvidence,
    TransferTaskSpec,
)


def _difficulty(steps: int = 3) -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=steps,
        symbolic_depth=1,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=0,
    )


def _outline() -> CourseOutlineArtifact:
    return CourseOutlineArtifact.model_validate(
        {
            "title": "Algebra",
            "chapters": [
                {
                    "key": "linear",
                    "title": "Linear equations",
                    "purpose": "Solve linear equations.",
                    "objective_keys": ["linear-equations"],
                    "anchor_ids": ["anchor:linear"],
                    "lab_keys": ["linear-plot"],
                }
            ],
            "concepts": [
                {
                    "key": "linear-equations",
                    "label": "Linear equations",
                    "anchor_ids": ["anchor:linear"],
                }
            ],
            "dependency_edges": [],
        }
    )


def _transfer(*, anchor_ids: tuple[str, ...] = ()) -> TransferTaskSpec:
    return TransferTaskSpec(
        key="linear-inverse",
        prompt="Construct an equation whose solution is x = 4.",
        invariant_concept_keys=["linear-equations"],
        dimensions=["inverse_or_constructive"],
        change_evidence=[
            TransferDimensionEvidence(
                dimension="inverse_or_constructive",
                source_structure="solve a supplied equation",
                target_structure="construct one from its solution",
                rationale="The goal direction changes while the concept remains.",
            )
        ],
        answer_type="numeric",
        difficulty=_difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        anchor_ids=anchor_ids,
    )


def _source() -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key="linear-source",
        chapter_key="linear",
        prompt="Exercise 3.1. Solve x + 2 = 6.",
        concept_keys=["linear-equations"],
        exercise_type="source_practice",
        answer_type="numeric",
        source_anchor_ids=["anchor:linear"],
        source_number="3.1",
        difficulty=_difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_source_level=True,
    )


def _core(**updates: object) -> ExerciseBlueprint:
    values: dict[str, object] = {
        "key": "linear-core",
        "chapter_key": "linear",
        "prompt": "Solve x + 2 = 6 and justify the inverse operation.",
        "concept_keys": ["linear-equations"],
        "exercise_type": "generated_core",
        "answer_type": "numeric",
        "source_anchor_ids": ["anchor:linear"],
        "difficulty": _difficulty(),
        "grader": NumericGraderSpec(kind="numeric", expected="4"),
        "hints": ("Identify.", "Represent.", "Solve.", "Check."),
        "is_core": True,
        "is_gating": True,
        "transfer_task": _transfer(),
    }
    values.update(updates)
    return ExerciseBlueprint.model_validate(values)


def _codes(findings: Sequence[ValidationFinding]) -> set[str]:
    return {finding.item_key for finding in findings}


@pytest.mark.parametrize(
    ("block_key", "quote", "category", "source_number"),
    [
        (
            "definition-2-1",
            "Definition 2.1. An exercise is a task used to practice a concept.",
            "definition",
            "2.1",
        ),
        (
            "theorem-4",
            "Theorem 4. The solution to this problem follows from continuity.",
            "theorem",
            "4",
        ),
        (
            "exercise-7",
            "Exercise 7. Solve the equation. Solution: x = 3.",
            "exercise",
            "7",
        ),
    ],
)
def test_explicit_textbook_label_wins_over_incidental_words(
    block_key: str, quote: str, category: str, source_number: str
) -> None:
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    anchor = evidence.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256="a" * 64,
        kind="pdf_page",
        index=1,
        block_key=block_key,
        quote=quote,
        source_role="PRIMARY",
    )

    classification = evidence.classify_assessment_anchor(anchor)

    assert classification.category == category
    assert classification.confidence == "high"
    assert classification.source_number == source_number


def test_cross_reference_to_an_exercise_is_not_a_confirmed_exercise() -> None:
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    anchor = evidence.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256="a" * 64,
        kind="pdf_page",
        index=1,
        block_key="text-1",
        quote="See Exercise 3.1 for details.",
        source_role="PRIMARY",
    )

    classification = evidence.classify_assessment_anchor(anchor)

    assert classification.confidence != "high"


def test_source_number_must_belong_to_the_confirmed_label() -> None:
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    anchor = evidence.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256="a" * 64,
        kind="pdf_page",
        index=1,
        block_key="text-1",
        quote="Worked Example. See Exercise 3.1 for details.",
        source_role="PRIMARY",
    )

    classification = evidence.classify_assessment_anchor(anchor)

    assert classification.category == "worked_example"
    assert classification.confidence == "high"
    assert classification.source_number is None


def test_medium_confidence_label_cannot_confirm_a_textbook_baseline() -> None:
    findings = AssessmentService.validate_bank(
        [_source(), _core()],
        known_anchor_ids={"anchor:linear"},
        classifications=[
            EvidenceClassification(
                anchor_id="anchor:linear",
                category="exercise",
                confidence="medium",
                source_number="3.1",
            )
        ],
    )

    assert "missing_difficulty_baseline" in _codes(findings)


def test_bank_requires_an_explicit_independent_transfer_review() -> None:
    findings = AssessmentService.validate_bank(
        [_source(), _core()],
        known_anchor_ids={"anchor:linear"},
        classifications=[
            EvidenceClassification(
                anchor_id="anchor:linear",
                category="exercise",
                confidence="high",
                source_number="3.1",
            )
        ],
        require_independent_review=True,
    )

    assert "manual_check" in _codes(findings)


def test_outline_rejects_unknown_concept_and_cross_chapter_anchor() -> None:
    exercise = _core(
        concept_keys=["invented-concept"],
        source_anchor_ids=["anchor:other"],
        transfer_task=_transfer(anchor_ids=("anchor:other",)),
    )

    findings = AssessmentService.validate_bank(
        [exercise],
        known_anchor_ids={"anchor:other"},
        expected_chapter_keys={"linear"},
        expected_concept_keys_by_chapter={"linear": {"linear-equations"}},
        expected_anchor_ids_by_chapter={"linear": {"anchor:linear"}},
    )

    assert {"unknown_concept_key", "anchor_outside_chapter"} <= _codes(findings)


def test_transfer_answer_type_must_match_its_grader() -> None:
    with pytest.raises(ValidationError, match="answer_type must match"):
        TransferTaskSpec(
            key="proof-with-numeric-grader",
            prompt="Prove the uniqueness claim.",
            invariant_concept_keys=["linear-equations"],
            dimensions=["proof_counterexample_generalization"],
            answer_type="proof",
            difficulty=_difficulty(),
            grader=NumericGraderSpec(kind="numeric", expected="1"),
        )


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo pwned')",
        "<script>alert(1)</script>",
        "```python\nprint('unsafe')\n```",
        "open(x)",
        "eval(x)",
        "exec(x)",
        "globals()",
        "os.system(x)",
    ],
)
def test_symbolic_grader_rejects_executable_expression_syntax(
    expression: str,
) -> None:
    with pytest.raises(ValidationError, match="unsafe|executable"):
        SymbolicGraderSpec(
            kind="symbolic",
            expected_expression=expression,
            allowed_symbols=["x"],
        )


def test_symbolic_grader_rejects_an_undeclared_symbol() -> None:
    with pytest.raises(ValidationError, match="undeclared"):
        SymbolicGraderSpec(
            kind="symbolic",
            expected_expression="x + y",
            allowed_symbols=["x"],
        )


@pytest.mark.asyncio
async def test_default_outline_loader_rejects_a_stale_version(monkeypatch) -> None:
    outline = _outline().model_dump(mode="json")
    course = Course(
        id="course:one",
        title="Algebra",
        notebook="notebook:one",
        outline=outline,
        outline_version_id="course_version:current",
    )
    stale = CourseVersion(
        id="course_version:stale",
        course="course:one",
        version_no=1,
        outline_artifact=outline,
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=stale))

    with pytest.raises(EvidenceInputError, match="current outline"):
        await AssessmentService()._load_current_outline(
            "course:one", "course_version:stale"
        )


@pytest.mark.asyncio
async def test_default_outline_loader_rejects_a_changed_hash(monkeypatch) -> None:
    outline = _outline().model_dump(mode="json")
    course = Course(
        id="course:one",
        title="Algebra",
        notebook="notebook:one",
        outline=outline,
        outline_version_id="course_version:current",
    )
    version = CourseVersion(
        id="course_version:current",
        course="course:one",
        version_no=1,
        outline_artifact=outline,
        outline_hash="0" * 64,
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))

    with pytest.raises(EvidenceInputError, match="hash"):
        await AssessmentService()._load_current_outline(
            "course:one", "course_version:current"
        )


@pytest.mark.asyncio
async def test_default_outline_loader_accepts_the_current_draft_candidate(
    monkeypatch,
) -> None:
    outline = _outline().model_dump(mode="json")
    course = Course(
        id="course:one",
        title="Algebra",
        notebook="notebook:one",
        outline=outline,
        outline_version_id="course_version:current",
    )
    version = CourseVersion(
        id="course_version:current",
        course="course:one",
        version_no=1,
        outline_artifact=outline,
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))

    loaded = await AssessmentService()._load_current_outline(
        "course:one", "course_version:current"
    )

    assert loaded == _outline()


@pytest.mark.asyncio
async def test_generation_receives_outline_context_and_rejects_unknown_chapter() -> (
    None
):
    bank = ExerciseBankArtifact(exercises=[_source(), _core(chapter_key="invented")])
    adapter = FakeCourseModelAdapter(bank)
    outline = _outline()

    with pytest.raises(ValueError, match="unknown outline chapter"):
        await CourseGenerationService(adapter).generate_exercise_bank(
            course_id="course:one",
            course_version_id="course_version:current",
            anchor_ids=["anchor:linear"],
            evidence=["[anchor:linear] Exercise 3.1"],
            classifications=[
                EvidenceClassification(
                    anchor_id="anchor:linear",
                    category="exercise",
                    confidence="high",
                    source_number="3.1",
                )
            ],
            outline=outline,
            model=ModelSelection(
                adapter="codex_cli",
                model="gpt-5.6-sol",
                reasoning_effort="max",
            ),
        )

    assert '"linear"' in adapter.calls[0].prompt
    assert '"linear-equations"' in adapter.calls[0].prompt
