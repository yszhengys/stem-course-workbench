from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from open_notebook.course.v2_contracts import (
    AdvisoryGraderSpec,
    BundleFileManifest,
    BundleRecordCount,
    ConceptMastery,
    CourseBundleManifest,
    DifficultyVector,
    DraftRevision,
    ExerciseBlueprint,
    LearningEvent,
    MultipartGraderSpec,
    NumericGraderSpec,
    ReplaceFormulaOperation,
    ReplaceTextOperation,
    ReviewQueueItem,
    SymbolicGraderSpec,
    TransferTaskSpec,
    TutorResponse,
    TutorTurn,
    UnitGraderSpec,
    VectorGraderSpec,
)


def difficulty() -> DifficultyVector:
    return DifficultyVector(
        concept_count=2,
        reasoning_steps=4,
        symbolic_depth=3,
        representation_shifts=1,
        proof_burden=0,
        physics_constraints=1,
    )


def test_v2_contracts_are_strict_and_frozen() -> None:
    with pytest.raises(ValidationError, match="unexpected"):
        DifficultyVector(
            concept_count=1,
            reasoning_steps=1,
            symbolic_depth=1,
            representation_shifts=0,
            proof_burden=0,
            physics_constraints=0,
            unexpected=True,  # type: ignore[call-arg]
        )

    vector = difficulty()
    with pytest.raises(ValidationError, match="frozen"):
        vector.reasoning_steps = 8  # type: ignore[misc]


@pytest.mark.parametrize(
    "payload",
    [
        {"concept_count": -1},
        {"reasoning_steps": 21},
        {"symbolic_depth": 21},
        {"representation_shifts": -1},
        {"proof_burden": 21},
        {"physics_constraints": -1},
    ],
)
def test_difficulty_vector_has_transparent_bounded_dimensions(
    payload: dict[str, int],
) -> None:
    values = difficulty().model_dump()
    values.update(payload)
    with pytest.raises(ValidationError):
        DifficultyVector.model_validate(values)


def test_objective_and_advisory_graders_are_discriminated() -> None:
    numeric = NumericGraderSpec(
        kind="numeric",
        expected="9.81",
        absolute_tolerance=0.01,
        relative_tolerance=0.001,
    )
    symbolic = SymbolicGraderSpec(
        kind="symbolic",
        expected_expression="x**2 + 2*x + 1",
        allowed_symbols=["x"],
    )
    unit = UnitGraderSpec(
        kind="unit",
        expected_value="9.81",
        expected_unit="m/s^2",
        relative_tolerance=0.001,
    )
    vector = VectorGraderSpec(
        kind="vector",
        expected_components=["1", "-2"],
        expected_unit="m/s",
        absolute_tolerance=0.001,
    )
    multipart = MultipartGraderSpec(
        kind="multipart",
        parts=[numeric, symbolic, unit, vector],
    )
    advisory = AdvisoryGraderSpec(kind="advisory", rubric="Give a proof.")

    assert multipart.parts[0].kind == "numeric"
    assert advisory.grants_mastery is False
    with pytest.raises(ValidationError):
        AdvisoryGraderSpec(kind="advisory", rubric="Give a proof.", grants_mastery=True)


def test_transfer_requires_invariant_concept_and_deep_dimension() -> None:
    transfer = TransferTaskSpec(
        key="projectile-inverse",
        prompt="Infer the launch angle from the observed range.",
        invariant_concept_keys=["projectile-motion"],
        dimensions=["inverse_or_constructive"],
        answer_type="numeric",
        difficulty=difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="45"),
        anchor_ids=["anchor:projectile"],
    )
    assert transfer.dimensions == ("inverse_or_constructive",)

    with pytest.raises(ValidationError, match="invariant_concept_keys"):
        TransferTaskSpec(
            key="invalid",
            prompt="Change 2 to 3.",
            invariant_concept_keys=[],
            dimensions=["representation"],
            answer_type="numeric",
            difficulty=difficulty(),
            grader=NumericGraderSpec(kind="numeric", expected="3"),
            anchor_ids=[],
        )


def test_exercise_blueprint_requires_grounded_textbook_core() -> None:
    transfer = TransferTaskSpec(
        key="limit-counterexample",
        prompt="Construct a counterexample under a changed boundary condition.",
        invariant_concept_keys=["limit"],
        dimensions=["proof_counterexample_generalization"],
        answer_type="symbolic",
        difficulty=difficulty(),
        grader=SymbolicGraderSpec(
            kind="symbolic", expected_expression="x", allowed_symbols=["x"]
        ),
        anchor_ids=["anchor:limit"],
    )
    blueprint = ExerciseBlueprint(
        key="limits-core-1",
        chapter_key="limits",
        prompt="Evaluate the limit and justify each transformation.",
        concept_keys=["limit"],
        exercise_type="worked_source",
        answer_type="symbolic",
        source_anchor_ids=["anchor:limit"],
        source_number="3.1.7",
        difficulty=difficulty(),
        grader=SymbolicGraderSpec(
            kind="symbolic", expected_expression="1", allowed_symbols=[]
        ),
        is_core=True,
        is_gating=True,
        is_source_level=True,
        transfer_task=transfer,
    )
    assert blueprint.transfer_task is not None
    assert blueprint.is_source_level is True

    with pytest.raises(ValidationError, match="source_anchor_ids"):
        ExerciseBlueprint.model_validate(
            {**blueprint.model_dump(), "source_anchor_ids": []}
        )


def test_core_and_mastery_eligible_exercises_require_objective_graders() -> None:
    advisory_transfer = TransferTaskSpec(
        key="limit-proof-transfer",
        prompt="Prove the changed boundary case.",
        invariant_concept_keys=["limit"],
        dimensions=["proof_counterexample_generalization"],
        answer_type="proof",
        difficulty=difficulty(),
        grader=AdvisoryGraderSpec(kind="advisory", rubric="Check the proof."),
        anchor_ids=["anchor:limit"],
    )
    base = {
        "key": "limits-core-objective",
        "chapter_key": "limits",
        "prompt": "Evaluate the source-grounded limit.",
        "concept_keys": ["limit"],
        "exercise_type": "worked_source",
        "source_anchor_ids": ["anchor:limit"],
        "difficulty": difficulty(),
        "is_core": True,
        "is_gating": True,
        "is_source_level": True,
    }

    with pytest.raises(ValidationError, match="objective grader"):
        ExerciseBlueprint(
            **base,
            answer_type="proof",
            grader=AdvisoryGraderSpec(kind="advisory", rubric="Check the proof."),
        )

    with pytest.raises(ValidationError, match="transfer gate"):
        ExerciseBlueprint(
            **base,
            answer_type="numeric",
            grader=NumericGraderSpec(kind="numeric", expected="1"),
            transfer_task=advisory_transfer,
        )


def test_learning_and_mastery_contracts_are_replayable() -> None:
    occurred_at = datetime(2026, 8, 21, tzinfo=timezone.utc)
    event = LearningEvent(
        event_id="event-001",
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_key="limits",
        concept_key="limit",
        exercise_key="limits-core-1",
        kind="graded_correct",
        payload={
            "answer_revealed": False,
            "hints_used": 0,
            "attempt_key": "attempt-001",
            "response_parts": ["1"],
        },
        occurred_at=occurred_at,
    )
    mastery = ConceptMastery(
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_key="limits",
        concept_key="limit",
        status="mastered",
        successful_exercise_keys=["limits-core-1", "limits-core-2"],
        unrevealed_success_count=1,
        review_level=0,
        review_due_at=occurred_at,
        snapshot_hash="a" * 64,
    )
    review = ReviewQueueItem(
        chapter_key="limits",
        concept_key="limit",
        status="review_due",
        due_at=occurred_at,
        interval_days=1,
    )

    assert event.kind == "graded_correct"
    assert mastery.successful_exercise_keys == ("limits-core-1", "limits-core-2")
    assert review.interval_days == 1


def test_tutor_draft_and_bundle_contracts_preserve_audit_data() -> None:
    turn = TutorTurn(
        turn_no=1,
        role="assistant",
        content="The definition follows from the cited source.",
        anchor_ids=["anchor:limit"],
        answer_revealed=False,
    )
    response = TutorResponse(
        session_id="course_tutor_session:one",
        turn=turn,
        insufficient_evidence=False,
    )
    revision = DraftRevision(
        revision_no=2,
        parent_revision_no=1,
        base_artifact_hash="a" * 64,
        artifact_hash="b" * 64,
        operation=ReplaceFormulaOperation(
            kind="replace_formula",
            block_key="formula-1",
            latex="E = mc^2",
            anchor_ids=["anchor:relativity"],
        ),
        invalidated_checks=["formula", "unit", "numeric"],
        created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
    )
    manifest = CourseBundleManifest(
        schema_version=1,
        app_version="2.0.0-dev",
        course_title="Calculus",
        exported_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        record_counts=[
            BundleRecordCount(record_type="course", count=1),
            BundleRecordCount(record_type="chapter", count=2),
        ],
        files=[
            BundleFileManifest(
                path="records/course.json",
                size_bytes=200,
                sha256="c" * 64,
            )
        ],
    )

    assert response.turn.anchor_ids == ("anchor:limit",)
    assert revision.operation.kind == "replace_formula"
    assert manifest.files[0].path == "records/course.json"


def test_draft_target_accepts_bounded_opaque_v1_and_synthesized_keys() -> None:
    opaque = ReplaceFormulaOperation(
        kind="replace_formula",
        block_key="Formula 1 (legacy)",
        latex="x=1",
        anchor_ids=(),
    )
    synthesized = ReplaceTextOperation(
        kind="replace_text",
        block_key=f"worked-example-{'x' * 100}-step-50",
        text="Updated step.",
        anchor_ids=(),
    )

    assert opaque.block_key == "Formula 1 (legacy)"
    assert len(synthesized.block_key) > 100
