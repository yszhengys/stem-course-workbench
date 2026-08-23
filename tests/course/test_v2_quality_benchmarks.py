"""Auditable quality gates over original algebra, calculus, and mechanics fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.v2_contracts import (
    EvidenceClassification,
    ExerciseBlueprint,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "v2"
FIXTURE_PATHS = tuple(sorted(FIXTURE_ROOT.glob("*.json")))


def _fixture(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _exercises(fixture: dict[str, Any]) -> tuple[ExerciseBlueprint, ...]:
    return tuple(
        ExerciseBlueprint.model_validate(raw)
        for raw in cast(list[dict[str, Any]], fixture["exercises"])
    )


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda path: path.stem)
def test_fixture_has_open_origin_worked_answer_and_source_evidence(
    path: Path,
) -> None:
    fixture = _fixture(path)

    assert fixture["fixture_version"] == 1
    assert fixture["license"] == "CC0-1.0"
    assert "not copied" in str(fixture["generation_note"])
    worked = cast(dict[str, Any], fixture["worked_example"])
    assert worked["prompt"] and worked["answer"]
    assert len(cast(list[str], worked["steps"])) >= 3
    evidence = cast(list[dict[str, Any]], fixture["source_evidence"])
    assert sum(item["category"] == "exercise" for item in evidence) >= 2
    assert any(item["category"] == "answer" for item in evidence)
    assert all(item["source_number"] for item in evidence)
    assert fixture["figure_description"]
    if fixture["subject"] == "physics":
        assert "unit" in str(fixture["unit_expectation"]).lower()
        assert any(item["category"] == "figure" for item in evidence)


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda path: path.stem)
def test_fixture_bank_meets_source_core_challenge_and_transfer_gates(
    path: Path,
) -> None:
    fixture = _fixture(path)
    exercises = _exercises(fixture)
    evidence = cast(list[dict[str, Any]], fixture["source_evidence"])
    anchor_ids = {str(item["anchor_id"]) for item in evidence}
    classifications = tuple(
        EvidenceClassification(
            anchor_id=str(item["anchor_id"]),
            category=cast(Any, item["category"]),
            confidence="high",
            source_number=str(item["source_number"]),
        )
        for item in evidence
    )
    chapter_key = exercises[0].chapter_key
    concept_keys = set(exercises[0].concept_keys)

    findings = AssessmentService.validate_bank(
        exercises,
        known_anchor_ids=anchor_ids,
        classifications=classifications,
        expected_chapter_keys={chapter_key},
        expected_concept_keys_by_chapter={chapter_key: concept_keys},
        expected_anchor_ids_by_chapter={chapter_key: anchor_ids},
    )

    assert findings == []
    source_levels = [item for item in exercises if item.is_source_level]
    core = next(item for item in exercises if item.is_core)
    challenge = next(
        item for item in exercises if item.exercise_type == "generated_challenge"
    )
    low_source = min(source_levels, key=lambda item: sum(item.difficulty.as_tuple()))
    high_source = max(source_levels, key=lambda item: sum(item.difficulty.as_tuple()))
    assert AssessmentService.dominates(core.difficulty, low_source.difficulty)
    assert AssessmentService.dominates(challenge.difficulty, core.difficulty)
    assert challenge.difficulty != core.difficulty
    assert AssessmentService.dominates(high_source.difficulty, challenge.difficulty)
    assert core.transfer_task is not None
    assert set(core.transfer_task.invariant_concept_keys) == set(core.concept_keys)


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda path: path.stem)
def test_every_fixture_answer_is_deterministic_and_mastery_eligible(
    path: Path,
) -> None:
    fixture = _fixture(path)
    answers = cast(dict[str, object], fixture["correct_answers"])
    exercises = _exercises(fixture)

    for exercise in exercises:
        assert exercise.key in answers
        first = AssessmentService.grade(exercise, answers[exercise.key])
        second = AssessmentService.grade(exercise, answers[exercise.key])
        assert first == second
        assert first.correct is True
        assert first.advisory is False
        assert first.grants_mastery is True
        if exercise.transfer_task is not None:
            transfer = exercise.transfer_task
            assert transfer.key in answers
            transfer_grade = AssessmentService.grade_transfer(
                transfer,
                answers[transfer.key],
            )
            assert transfer_grade.correct is True
            assert transfer_grade.grants_mastery is True


def test_quality_fixture_set_covers_required_v2_subjects() -> None:
    fixtures = [_fixture(path) for path in FIXTURE_PATHS]

    assert {fixture["topic"] for fixture in fixtures} == {
        "linear-equations",
        "limits",
        "constant-acceleration",
    }
    assert {fixture["subject"] for fixture in fixtures} == {"math", "physics"}
