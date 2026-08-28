"""Contract tests for honest academic verification metadata."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from open_notebook.course.contracts import (
    AcademicVerification,
    ExerciseArtifact,
    FormulaArtifact,
    WorkedExampleArtifact,
)

_ARTIFACT_HASH = "a" * 64


@pytest.mark.parametrize(
    "payload",
    [
        {
            "level": "L0",
            "method": "structure",
            "anchor_ids": [],
            "reason": None,
            "verified_at": None,
            "artifact_hash": None,
        },
        {
            "level": "L1",
            "method": "self_consistency",
            "anchor_ids": [],
            "reason": None,
            "verified_at": None,
            "artifact_hash": None,
        },
        {
            "level": "L1",
            "method": "independent_model_review",
            "anchor_ids": [],
            "reason": "A second model checked internal consistency only.",
            "verified_at": None,
            "artifact_hash": None,
        },
        {
            "level": "L2",
            "method": "source_answer",
            "anchor_ids": ["anchor:answer_key"],
            "reason": None,
            "verified_at": None,
            "artifact_hash": _ARTIFACT_HASH,
        },
        {
            "level": "L2",
            "method": "deterministic_solver",
            "anchor_ids": [],
            "reason": "SymPy reproduced the displayed answer.",
            "verified_at": None,
            "artifact_hash": _ARTIFACT_HASH,
        },
        {
            "level": "L3",
            "method": "human_review",
            "anchor_ids": ["anchor:answer_key"],
            "reason": "Checked line by line against the cited answer key.",
            "verified_at": datetime(2026, 8, 29, tzinfo=timezone.utc),
            "artifact_hash": _ARTIFACT_HASH,
        },
    ],
)
def test_academic_verification_accepts_exact_supported_combinations(
    payload: dict[str, object],
) -> None:
    verification = AcademicVerification.model_validate(payload)

    assert verification.level == payload["level"]


@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {"level": "L2", "method": "source_answer"},
            "anchors or solver provenance",
        ),
        (
            {
                "level": "L3",
                "method": "human_review",
                "anchor_ids": ["anchor:answer_key"],
                "verified_at": datetime(2026, 8, 29, tzinfo=timezone.utc),
                "artifact_hash": _ARTIFACT_HASH,
            },
            "reason",
        ),
        (
            {
                "level": "L3",
                "method": "human_review",
                "reason": "Checked.",
                "verified_at": datetime(2026, 8, 29, tzinfo=timezone.utc),
                "artifact_hash": _ARTIFACT_HASH,
            },
            "anchor",
        ),
        (
            {
                "level": "L3",
                "method": "human_review",
                "anchor_ids": ["anchor:answer_key"],
                "reason": "Checked.",
                "artifact_hash": _ARTIFACT_HASH,
            },
            "timestamp",
        ),
        (
            {
                "level": "L3",
                "method": "human_review",
                "anchor_ids": ["anchor:answer_key"],
                "reason": "Checked.",
                "verified_at": datetime(2026, 8, 29, tzinfo=timezone.utc),
            },
            "artifact hash",
        ),
    ],
)
def test_academic_verification_rejects_unsubstantiated_l2_and_l3(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        AcademicVerification.model_validate(payload)


def test_academic_verification_rejects_non_utc_timestamps_and_unstable_anchors() -> None:
    with pytest.raises(ValidationError, match="UTC"):
        AcademicVerification(
            level="L3",
            method="human_review",
            anchor_ids=["anchor:answer_key"],
            reason="Checked.",
            verified_at=datetime(
                2026, 8, 29, tzinfo=timezone(timedelta(hours=8))
            ),
            artifact_hash=_ARTIFACT_HASH,
        )

    with pytest.raises(ValidationError, match="stable anchor IDs"):
        AcademicVerification(
            level="L3",
            method="human_review",
            anchor_ids=["page 1"],
            reason="Checked.",
            verified_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            artifact_hash=_ARTIFACT_HASH,
        )


def test_legacy_academic_artifacts_receive_an_explicit_l1_default() -> None:
    formula = FormulaArtifact(
        key="formula",
        latex="x^2",
        meaning="Square a value.",
        anchor_ids=[],
        provenance="derived",
    )
    example = WorkedExampleArtifact(
        key="example",
        prompt="Compute 2 + 2.",
        steps=["Add the terms."],
        answer="4",
        anchor_ids=[],
        provenance="pedagogical",
    )
    exercise = ExerciseArtifact(
        key="exercise",
        prompt="Compute 3 + 3.",
        difficulty="core",
        hints=[],
        answer="6",
        transfer_task="Compute 4 + 4.",
        anchor_ids=[],
        provenance="pedagogical",
    )

    for artifact in (formula, example, exercise):
        assert artifact.verification.level == "L1"
        assert artifact.verification.method == "self_consistency"
        assert artifact.model_dump(mode="json")["verification"] == {
            "level": "L1",
            "method": "self_consistency",
            "anchor_ids": [],
            "reason": None,
            "verified_at": None,
            "artifact_hash": None,
        }
