from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from open_notebook.course.v2_contracts import LearningEvent

NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        (
            "graded_correct",
            {"answer_revealed": False, "hints_used": 0},
        ),
        ("hint_viewed", {"hint_index": 1}),
        ("answer_revealed", {"transfer_task_key": "transfer-one"}),
    ],
)
def test_exercise_events_require_an_exercise_key(
    kind: str, payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="exercise_key"):
        LearningEvent.model_validate(
            {
                "event_id": "event-one",
                "course_id": "course:one",
                "course_version_id": "course_version:one",
                "chapter_key": "limits",
                "concept_key": "limit",
                "kind": kind,
                "payload": payload,
                "occurred_at": NOW,
            }
        )


def test_mastery_events_require_a_concept_key() -> None:
    with pytest.raises(ValidationError, match="concept_key"):
        LearningEvent(
            event_id="event-two",
            course_id="course:one",
            course_version_id="course_version:one",
            chapter_key="limits",
            exercise_key="limits-core",
            kind="graded_correct",
            payload={"answer_revealed": False, "hints_used": 0},
            occurred_at=NOW,
        )


def test_reading_position_requires_a_stable_block_key() -> None:
    with pytest.raises(ValidationError, match="block_key"):
        LearningEvent(
            event_id="event-three",
            course_id="course:one",
            course_version_id="course_version:one",
            chapter_key="limits",
            kind="reading_position",
            payload={},
            occurred_at=NOW,
        )


def test_transfer_requirement_is_an_explicit_replayable_event() -> None:
    event = LearningEvent(
        event_id="event-four",
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_key="limits",
        concept_key="limit",
        exercise_key="limits-core",
        kind="transfer_required",
        payload={"transfer_task_key": "transfer-one"},
        occurred_at=NOW,
    )

    assert event.kind == "transfer_required"
