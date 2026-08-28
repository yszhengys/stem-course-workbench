import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import open_notebook.course.assessment_service as assessment_module
from open_notebook.course.learning_service import _APPEND_LOCKS, LearningService
from open_notebook.course.v2_contracts import (
    ConceptMastery,
    DifficultyVector,
    ExerciseBlueprint,
    LearningEvent,
    NumericGraderSpec,
    SymbolicGraderSpec,
    TransferTaskSpec,
)
from open_notebook.exceptions import InvalidInputError

START = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)


def _difficulty() -> DifficultyVector:
    return DifficultyVector(
        concept_count=1,
        reasoning_steps=2,
        symbolic_depth=1,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=0,
    )


def _blueprint(
    key: str,
    *,
    core: bool = False,
    source_level: bool = False,
    hints: tuple[str, ...] = (),
) -> ExerciseBlueprint:
    transfer = (
        TransferTaskSpec(
            key="linear-transfer",
            prompt="Solve the invariant in a changed representation.",
            invariant_concept_keys=["linear-equations"],
            dimensions=["representation"],
            answer_type="numeric",
            difficulty=_difficulty(),
            grader=NumericGraderSpec(kind="numeric", expected="8"),
            anchor_ids=["anchor:linear"],
        )
        if core
        else None
    )
    return ExerciseBlueprint(
        key=key,
        chapter_key="linear",
        prompt="Solve the source-grounded linear exercise.",
        concept_keys=["linear-equations"],
        exercise_type="generated_core" if core else "source_practice",
        answer_type="numeric",
        hints=hints,
        source_anchor_ids=["anchor:linear"],
        source_number="3.1" if source_level else None,
        difficulty=_difficulty(),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_core=core,
        is_gating=core,
        is_source_level=source_level,
        transfer_task=transfer,
    )


EXERCISES = {
    "linear-core": _blueprint("linear-core", core=True),
    "linear-source": _blueprint("linear-source", source_level=True),
}


def _parts(value: object) -> tuple[str, ...]:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")),)


def _event(
    event_id: str,
    kind: str,
    exercise_key: str,
    payload: dict[str, object],
    *,
    at: datetime,
) -> LearningEvent:
    return LearningEvent(
        event_id=event_id,
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_key="linear",
        concept_key="linear-equations",
        exercise_key=exercise_key,
        kind=kind,
        payload=payload,
        occurred_at=at,
    )


def _graded(
    event_id: str,
    exercise_key: str,
    *,
    at: datetime,
    attempt: str | None = None,
    answer: object = "4",
    correct: bool = True,
    revealed: bool = False,
    hints: int = 0,
) -> LearningEvent:
    return _event(
        event_id,
        "graded_correct" if correct else "graded_incorrect",
        exercise_key,
        {
            "attempt_key": attempt or f"attempt-{event_id}",
            "response_parts": _parts(answer),
            "answer_revealed": revealed,
            "hints_used": hints,
        },
        at=at,
    )


def _hint(
    event_id: str,
    exercise_key: str,
    attempt: str,
    index: int,
    *,
    at: datetime,
) -> LearningEvent:
    return _event(
        event_id,
        "hint_viewed",
        exercise_key,
        {"attempt_key": attempt, "hint_index": index},
        at=at,
    )


def _reveal(
    event_id: str, exercise_key: str, attempt: str, *, at: datetime
) -> LearningEvent:
    return _event(
        event_id,
        "answer_revealed",
        exercise_key,
        {
            "attempt_key": attempt,
            "transfer_task_key": (
                "linear-transfer" if exercise_key == "linear-core" else None
            ),
        },
        at=at,
    )


def _required(event_id: str, attempt: str, *, at: datetime) -> LearningEvent:
    return _event(
        event_id,
        "transfer_required",
        "linear-core",
        {"attempt_key": attempt, "transfer_task_key": "linear-transfer"},
        at=at,
    )


def _transfer(
    event_id: str,
    *,
    at: datetime,
    answer: object = "8",
    source_attempt: str = "attempt-core-revealed",
    attempt: str | None = None,
) -> LearningEvent:
    return _event(
        event_id,
        "transfer_completed",
        "linear-core",
        {
            "attempt_key": attempt or f"attempt-{event_id}",
            "source_attempt_key": source_attempt,
            "transfer_task_key": "linear-transfer",
            "response_parts": _parts(answer),
        },
        at=at,
    )


def _review(
    event_id: str,
    exercise_key: str,
    *,
    at: datetime,
    attempt: str | None = None,
    answer: object = "4",
    correct: bool = True,
    revealed: bool = False,
    hints: int = 0,
) -> LearningEvent:
    return _event(
        event_id,
        "review_completed",
        exercise_key,
        {
            "attempt_key": attempt or f"attempt-{event_id}",
            "response_parts": _parts(answer),
            "correct": correct,
            "answer_revealed": revealed,
            "hints_used": hints,
        },
        at=at,
    )


def _mastery_events() -> list[LearningEvent]:
    attempt = "attempt-source"
    return [
        _graded("core-correct", "linear-core", at=START),
        _reveal(
            "source-reveal",
            "linear-source",
            attempt,
            at=START + timedelta(seconds=30),
        ),
        _graded(
            "source-correct",
            "linear-source",
            attempt=attempt,
            revealed=True,
            at=START + timedelta(minutes=1),
        ),
    ]


def _reduce(
    events: list[LearningEvent], *, now: datetime | None = None
) -> ConceptMastery:
    return LearningService.reduce_events(events, exercises=EXERCISES, now=now)


def test_mastery_needs_two_distinct_qualifying_successes_and_one_unrevealed() -> None:
    mastery = _reduce(_mastery_events(), now=START + timedelta(minutes=1))

    assert mastery.status == "mastered"
    assert mastery.successful_exercise_keys == ("linear-core", "linear-source")
    assert mastery.unrevealed_success_count == 1
    assert mastery.review_due_at == START + timedelta(minutes=1, days=1)


def test_repeating_one_exercise_does_not_satisfy_two_exercise_rule() -> None:
    events = [
        _graded("first", "linear-core", at=START),
        _graded("second", "linear-core", at=START + timedelta(minutes=1)),
    ]
    mastery = _reduce(events, now=START + timedelta(minutes=1))

    assert mastery.status == "practiced"
    assert mastery.successful_exercise_keys == ("linear-core",)


def test_four_hints_cap_mastery_and_attempt_summary_cannot_be_spoofed() -> None:
    events: list[LearningEvent] = []
    for offset, exercise_key in enumerate(("linear-core", "linear-source")):
        attempt = f"attempt-hinted-{offset}"
        base = START + timedelta(minutes=offset)
        events.extend(
            _hint(
                f"hint-{offset}-{index}",
                exercise_key,
                attempt,
                index,
                at=base + timedelta(seconds=index),
            )
            for index in range(1, 5)
        )
        events.append(
            _graded(
                f"grade-{offset}",
                exercise_key,
                attempt=attempt,
                hints=4,
                at=base + timedelta(seconds=5),
            )
        )

    mastery = _reduce(events, now=START + timedelta(minutes=2))
    assert mastery.status == "practiced"

    spoofed = events[-1].model_copy(
        update={
            "event_id": "spoofed-summary",
            "payload": events[-1].payload.model_copy(update={"hints_used": 0}),
        }
    )
    with pytest.raises(ValueError, match="summary"):
        _reduce([*events[:-1], spoofed], now=START + timedelta(minutes=2))


def test_using_every_authored_hint_caps_mastery_when_fewer_than_four_exist() -> None:
    exercises = {
        "linear-core": _blueprint(
            "linear-core",
            core=True,
            hints=("Isolate the variable.", "Check the sign."),
        ),
        "linear-source": _blueprint(
            "linear-source",
            source_level=True,
            hints=("Collect like terms.", "Substitute to verify."),
        ),
    }
    events: list[LearningEvent] = []
    for offset, exercise_key in enumerate(exercises):
        attempt = f"attempt-all-hints-{offset}"
        base = START + timedelta(minutes=offset)
        events.extend(
            [
                _hint(
                    f"hint-{offset}-1",
                    exercise_key,
                    attempt,
                    1,
                    at=base + timedelta(seconds=1),
                ),
                _hint(
                    f"hint-{offset}-2",
                    exercise_key,
                    attempt,
                    2,
                    at=base + timedelta(seconds=2),
                ),
                _graded(
                    f"grade-all-hints-{offset}",
                    exercise_key,
                    attempt=attempt,
                    hints=2,
                    at=base + timedelta(seconds=3),
                ),
            ]
        )

    mastery = LearningService.reduce_events(
        events,
        exercises=exercises,
        now=START + timedelta(minutes=2),
    )

    assert mastery.status == "practiced"


def test_catalog_flags_are_not_client_controlled() -> None:
    payload = _graded("flags", "linear-core", at=START).model_dump(mode="json")
    assert isinstance(payload["payload"], dict)
    payload["payload"]["is_core"] = True

    with pytest.raises(ValidationError, match="extra"):
        LearningEvent.model_validate(payload)


def test_server_regrades_privileged_events_and_rejects_forged_outcomes() -> None:
    forged = _graded(
        "forged-grade",
        "linear-core",
        answer="5",
        correct=True,
        at=START,
    )
    with pytest.raises(ValueError, match="grade outcome"):
        _reduce([forged], now=START)

    with pytest.raises(ValueError, match="required"):
        _reduce([_transfer("forged-transfer", at=START)], now=START)


def test_symbolic_history_replay_uses_a_bounded_deterministic_cache(
    monkeypatch,
) -> None:
    calls = 0

    def inequivalent(*args, **kwargs):
        nonlocal calls
        calls += 1
        comparisons = json.loads(kwargs["input"])["comparisons"]
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps([False] * len(comparisons)),
            stderr="",
        )

    monkeypatch.setattr(
        "open_notebook.course.assessment_service.subprocess.run", inequivalent
    )
    symbolic = ExerciseBlueprint(
        key="linear-symbolic",
        chapter_key="linear",
        prompt="Simplify the expression.",
        concept_keys=["linear-equations"],
        exercise_type="source_practice",
        answer_type="symbolic",
        source_anchor_ids=["anchor:linear"],
        source_number="3.2",
        difficulty=_difficulty(),
        grader=SymbolicGraderSpec(
            kind="symbolic",
            expected_expression="x",
            allowed_symbols=["x"],
        ),
        is_source_level=True,
    )
    events = [
        LearningEvent(
            event_id=f"symbolic-wrong-{index}",
            course_id="course:one",
            course_version_id="course_version:one",
            chapter_key="linear",
            concept_key="linear-equations",
            exercise_key=symbolic.key,
            kind="graded_incorrect",
            payload={
                "attempt_key": f"symbolic-attempt-{index}",
                "response_parts": _parts(f"x + {314159 + index}"),
                "answer_revealed": False,
                "hints_used": 0,
            },
            occurred_at=START + timedelta(seconds=index),
        )
        for index in range(25)
    ]

    mastery = LearningService.reduce_events(
        events,
        exercises={symbolic.key: symbolic},
        now=START + timedelta(minutes=1),
    )

    assert mastery.status == "learning"
    assert calls == 1


def test_257_unique_symbolic_events_replay_identically_after_cache_reset(
    monkeypatch,
) -> None:
    calls = 0

    def equivalent(*args, **kwargs):
        nonlocal calls
        calls += 1
        comparisons = json.loads(kwargs["input"])["comparisons"]
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps([True] * len(comparisons)),
            stderr="",
        )

    monkeypatch.setattr(
        "open_notebook.course.assessment_service.subprocess.run", equivalent
    )
    symbolic = ExerciseBlueprint(
        key="linear-symbolic-replay",
        chapter_key="linear",
        prompt="Simplify the expression.",
        concept_keys=["linear-equations"],
        exercise_type="source_practice",
        answer_type="symbolic",
        source_anchor_ids=["anchor:linear"],
        source_number="3.3",
        difficulty=_difficulty(),
        grader=SymbolicGraderSpec(
            kind="symbolic", expected_expression="x", allowed_symbols=["x"]
        ),
        is_source_level=True,
    )
    events = [
        LearningEvent(
            event_id=f"symbolic-correct-{index}",
            course_id="course:one",
            course_version_id="course_version:one",
            chapter_key="linear",
            concept_key="linear-equations",
            exercise_key=symbolic.key,
            kind="graded_correct",
            payload={
                "attempt_key": f"symbolic-correct-attempt-{index}",
                "response_parts": _parts(f"x + ({index} - {index})"),
                "answer_revealed": False,
                "hints_used": 0,
            },
            occurred_at=START + timedelta(seconds=index),
        )
        for index in range(1, 258)
    ]

    assessment_module._SYMBOLIC_EQUIVALENCE_CACHE.clear()
    try:
        warm = LearningService.reduce_events(
            events,
            exercises={symbolic.key: symbolic},
            now=START + timedelta(minutes=5),
        )
        assessment_module._SYMBOLIC_EQUIVALENCE_CACHE.clear()
        cold = LearningService.reduce_events(
            events,
            exercises={symbolic.key: symbolic},
            now=START + timedelta(minutes=5),
        )
    finally:
        assessment_module._SYMBOLIC_EQUIVALENCE_CACHE.clear()

    assert warm == cold
    assert calls == 2


def test_symbolic_replay_limit_is_independent_of_warm_cache(monkeypatch) -> None:
    def equivalent(*args, **kwargs):
        comparisons = json.loads(kwargs["input"])["comparisons"]
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps([True] * len(comparisons)),
            stderr="",
        )

    monkeypatch.setattr(
        "open_notebook.course.assessment_service.subprocess.run", equivalent
    )
    grader = SymbolicGraderSpec(
        kind="symbolic", expected_expression="x", allowed_symbols=["x"]
    )
    inputs = [(grader, f"x + ({index} - {index})") for index in range(1, 514)]

    assessment_module._SYMBOLIC_EQUIVALENCE_CACHE.clear()
    try:
        assessment_module.AssessmentService.prime_symbolic_grades(inputs[:512])
        with pytest.raises(ValueError, match="comparison limit"):
            assessment_module.AssessmentService.prime_symbolic_grades(inputs)
        assessment_module._SYMBOLIC_EQUIVALENCE_CACHE.clear()
        with pytest.raises(ValueError, match="comparison limit"):
            assessment_module.AssessmentService.prime_symbolic_grades(inputs)
    finally:
        assessment_module._SYMBOLIC_EQUIVALENCE_CACHE.clear()


def test_revealed_core_requires_verified_transfer_before_mastery() -> None:
    attempt = "attempt-core-revealed"
    events = [
        _reveal("core-reveal", "linear-core", attempt, at=START),
        _required("transfer-required", attempt, at=START),
        _graded(
            "core-grade",
            "linear-core",
            attempt=attempt,
            revealed=True,
            at=START + timedelta(minutes=1),
        ),
        _graded(
            "source-grade",
            "linear-source",
            at=START + timedelta(minutes=2),
        ),
    ]

    blocked = _reduce(events, now=START + timedelta(minutes=2))
    completed = _reduce(
        [*events, _transfer("transfer-ok", at=START + timedelta(minutes=3))],
        now=START + timedelta(minutes=3),
    )

    assert blocked.status == "practiced"
    assert completed.status == "mastered"

    with pytest.raises(ValueError, match="transfer"):
        _reduce(
            [
                *events,
                _transfer(
                    "transfer-wrong",
                    answer="7",
                    at=START + timedelta(minutes=3),
                ),
            ],
            now=START + timedelta(minutes=3),
        )


def test_transfer_completion_cannot_skip_transfer_required() -> None:
    attempt = "attempt-core-revealed"
    events = [
        _reveal("core-reveal", "linear-core", attempt, at=START),
        _graded(
            "core-grade",
            "linear-core",
            attempt=attempt,
            revealed=True,
            at=START + timedelta(minutes=1),
        ),
        _graded(
            "source-grade",
            "linear-source",
            at=START + timedelta(minutes=2),
        ),
        _transfer("transfer-without-requirement", at=START + timedelta(minutes=3)),
    ]

    with pytest.raises(ValueError, match="required transfer"):
        _reduce(events, now=START + timedelta(minutes=3))


def test_transfer_attempt_cannot_reuse_a_regular_attempt_identity() -> None:
    reveal_attempt = "attempt-core-revealed"
    reused_attempt = "attempt-reused"
    events = [
        _reveal("core-reveal", "linear-core", reveal_attempt, at=START),
        _required("transfer-required", reveal_attempt, at=START),
        _graded(
            "core-grade",
            "linear-core",
            attempt=reveal_attempt,
            revealed=True,
            at=START + timedelta(minutes=1),
        ),
        _graded(
            "source-grade",
            "linear-source",
            attempt=reused_attempt,
            at=START + timedelta(minutes=2),
        ),
        _transfer(
            "transfer-reused",
            attempt=reused_attempt,
            at=START + timedelta(minutes=3),
        ),
    ]

    with pytest.raises(ValueError, match="attempt identity"):
        _reduce(events, now=START + timedelta(minutes=3))


def test_replay_is_order_independent_utc_canonical_and_hash_stable() -> None:
    events = _mastery_events()
    chronological = _reduce(events, now=START + timedelta(minutes=1))
    reversed_replay = _reduce(list(reversed(events)), now=START + timedelta(minutes=1))
    offset_event = _graded(
        "offset-event",
        "linear-core",
        at=datetime(2026, 8, 21, 17, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    utc_event = _graded("offset-event", "linear-core", at=START)

    first_offset = _reduce([offset_event, utc_event], now=START)
    first_utc = _reduce([utc_event, offset_event], now=START)

    assert chronological == reversed_replay
    assert first_offset == first_utc
    assert offset_event.occurred_at == START
    assert offset_event.occurred_at.tzinfo == timezone.utc
    assert len(chronological.snapshot_hash) == 64


def test_equal_timestamps_use_semantic_precedence_not_event_id() -> None:
    attempt = "attempt-same-time"
    reveal = _reveal("z-reveal", "linear-core", attempt, at=START)
    required = _required("y-required", attempt, at=START)
    grade = _graded(
        "a-grade",
        "linear-core",
        attempt=attempt,
        revealed=True,
        at=START,
    )

    first = _reduce([grade, required, reveal], now=START)
    second = _reduce([reveal, required, grade], now=START)

    assert first == second
    assert first.status == "practiced"


def test_conflicting_duplicate_event_id_fails_closed() -> None:
    first = _graded("duplicate", "linear-core", at=START)
    conflicting = _graded(
        "duplicate",
        "linear-source",
        at=START + timedelta(minutes=1),
    )

    with pytest.raises(ValueError, match="duplicate"):
        _reduce([first, conflicting], now=START + timedelta(minutes=1))


def test_learning_event_rejects_naive_or_unbounded_payload() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _graded(
            "naive-time",
            "linear-core",
            at=datetime(2026, 8, 21, 9, 0),
        )

    payload = _graded("large", "linear-core", at=START).model_dump(mode="json")
    assert isinstance(payload["payload"], dict)
    payload["payload"]["response_parts"] = ["x" * 4001]
    with pytest.raises(ValidationError, match="string_too_long"):
        LearningEvent.model_validate(payload)


def test_reduce_rejects_past_as_of_time_and_datetime_overflow() -> None:
    with pytest.raises(ValueError, match="before the last event"):
        _reduce(_mastery_events(), now=START)

    near_max = datetime.max.replace(tzinfo=timezone.utc)
    events = [
        _graded(
            "max-core",
            "linear-core",
            at=near_max - timedelta(minutes=1),
        ),
        _graded("max-source", "linear-source", at=near_max),
    ]
    with pytest.raises(ValueError, match="schedule"):
        _reduce(events, now=near_max)


def test_review_must_be_due_and_intervals_advance_reset_or_hold_when_revealed() -> None:
    mastered = _mastery_events()
    first_due = START + timedelta(minutes=1, days=1)
    early = _review(
        "review-early",
        "linear-core",
        at=first_due - timedelta(minutes=5),
    )
    with pytest.raises(ValueError, match="not due"):
        _reduce([*mastered, early], now=early.occurred_at)

    due = _reduce(mastered, now=first_due)
    review_one = _review("review-1", "linear-core", at=first_due)
    advanced = _reduce([*mastered, review_one], now=first_due)
    second_due = first_due + timedelta(days=3)
    attempt = "attempt-review-revealed"
    reveal = _reveal(
        "review-reveal",
        "linear-source",
        attempt,
        at=second_due,
    )
    revealed_review = _review(
        "review-2",
        "linear-source",
        attempt=attempt,
        revealed=True,
        at=second_due,
    )
    revealed = _reduce(
        [*mastered, review_one, reveal, revealed_review],
        now=second_due,
    )
    reset = _reduce(
        [
            *mastered,
            review_one,
            _review(
                "review-wrong",
                "linear-core",
                answer="5",
                correct=False,
                at=second_due,
            ),
        ],
        now=second_due,
    )
    revealed_wrong_attempt = "attempt-review-revealed-wrong"
    revealed_wrong = _reduce(
        [
            *mastered,
            review_one,
            _reveal(
                "review-wrong-reveal",
                "linear-source",
                revealed_wrong_attempt,
                at=second_due,
            ),
            _review(
                "review-wrong-revealed",
                "linear-source",
                attempt=revealed_wrong_attempt,
                answer="5",
                correct=False,
                revealed=True,
                at=second_due,
            ),
        ],
        now=second_due,
    )
    hinted_attempt = "attempt-review-hinted"
    hinted_events = [
        _hint(
            f"review-hint-{index}",
            "linear-core",
            hinted_attempt,
            index,
            at=second_due + timedelta(seconds=index),
        )
        for index in range(1, 5)
    ]
    hinted_review = _review(
        "review-hinted",
        "linear-core",
        attempt=hinted_attempt,
        hints=4,
        at=second_due + timedelta(seconds=5),
    )
    hinted = _reduce(
        [*mastered, review_one, *hinted_events, hinted_review],
        now=hinted_review.occurred_at,
    )

    assert due.status == "review_due"
    assert advanced.review_level == 1
    assert advanced.review_due_at == second_due
    assert revealed.status == "review_due"
    assert revealed.review_level == 1
    assert revealed.review_due_at == second_due
    assert reset.review_level == 0
    assert reset.review_due_at == second_due + timedelta(days=1)
    assert revealed_wrong.review_level == 0
    assert revealed_wrong.review_due_at == second_due + timedelta(days=1)
    assert hinted.review_level == 1
    assert hinted.review_due_at == second_due


def test_revealed_review_is_recorded_while_its_transfer_is_pending() -> None:
    first_due = START + timedelta(minutes=1, days=1)
    attempt = "attempt-revealed-review"
    events = [
        *_mastery_events(),
        _reveal("review-core-reveal", "linear-core", attempt, at=first_due),
        _required("review-transfer-required", attempt, at=first_due),
        _review(
            "review-revealed-core",
            "linear-core",
            attempt=attempt,
            revealed=True,
            at=first_due,
        ),
    ]

    pending = _reduce(events, now=first_due)
    completed = _reduce(
        [
            *events,
            _transfer(
                "review-transfer-completed",
                source_attempt=attempt,
                at=first_due + timedelta(minutes=1),
            ),
        ],
        now=first_due + timedelta(minutes=1),
    )

    assert pending.status == "practiced"
    assert pending.review_level == 0
    assert pending.review_due_at == first_due
    assert [item.model_dump(mode="json") for item in pending.pending_transfers] == [
        {
            "chapter_key": "linear",
            "concept_key": "linear-equations",
            "exercise_key": "linear-core",
            "source_attempt_key": attempt,
            "transfer_task_key": "linear-transfer",
        }
    ]
    assert completed.status == "review_due"
    assert completed.review_level == 0
    assert completed.review_due_at == first_due
    assert completed.pending_transfers == ()


@pytest.mark.asyncio
async def test_append_validates_before_write_and_replays_the_audit_log() -> None:
    events: list[LearningEvent] = []
    snapshots: list[ConceptMastery] = []

    async def append(event: LearningEvent) -> None:
        events.append(event)

    async def load_events(
        course_id: str,
        version_id: str,
        chapter_key: str,
        concept_key: str,
    ) -> tuple[LearningEvent, ...]:
        return tuple(events)

    async def load_exercises(
        course_id: str, version_id: str, chapter_key: str
    ) -> tuple[ExerciseBlueprint, ...]:
        return tuple(EXERCISES.values())

    async def save(mastery: ConceptMastery) -> None:
        snapshots.append(mastery)

    service = LearningService(
        event_appender=append,
        event_loader=load_events,
        exercise_loader=load_exercises,
        mastery_saver=save,
        clock=lambda: START + timedelta(minutes=5),
    )
    forged = _graded("forged", "linear-core", answer="5", correct=True, at=START)
    with pytest.raises(InvalidInputError, match="grade outcome"):
        await service.append_event(forged)
    assert events == []

    practiced = await service.append_event(_mastery_events()[0])
    await service.append_event(_mastery_events()[1])
    mastered = await service.append_event(_mastery_events()[2])

    assert practiced.status == "practiced"
    assert mastered.status == "mastered"
    assert snapshots[-1] == _reduce(events)


@pytest.mark.asyncio
async def test_append_rejects_excessive_future_timestamp_before_write() -> None:
    events: list[LearningEvent] = []

    async def append(event: LearningEvent) -> None:
        events.append(event)

    async def load_events(
        course_id: str,
        version_id: str,
        chapter_key: str,
        concept_key: str,
    ) -> tuple[LearningEvent, ...]:
        return tuple(events)

    async def load_exercises(
        course_id: str, version_id: str, chapter_key: str
    ) -> tuple[ExerciseBlueprint, ...]:
        return tuple(EXERCISES.values())

    async def save(mastery: ConceptMastery) -> None:
        return None

    service = LearningService(
        event_appender=append,
        event_loader=load_events,
        exercise_loader=load_exercises,
        mastery_saver=save,
        clock=lambda: START,
    )

    with pytest.raises(InvalidInputError, match="future"):
        await service.append_event(
            _graded(
                "future",
                "linear-core",
                at=START + timedelta(minutes=6),
            )
        )
    assert events == []


@pytest.mark.asyncio
async def test_activity_events_use_a_non_mastery_persistence_path() -> None:
    events: list[LearningEvent] = []

    async def append(event: LearningEvent) -> None:
        events.append(event)

    async def validate_scope(event: LearningEvent) -> None:
        assert event.course_id == "course:one"

    position = LearningEvent(
        event_id="position-one",
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_key="linear",
        kind="reading_position",
        payload={"block_key": "worked-example-one"},
        occurred_at=START,
    )
    service = LearningService(
        event_appender=append,
        scope_validator=validate_scope,
        clock=lambda: START,
    )

    stored = await service.append_activity_event(position)

    assert stored == position
    assert events == [position]
    with pytest.raises(InvalidInputError, match="append_activity_event"):
        await service.append_event(position)
    with pytest.raises(InvalidInputError, match="activity"):
        await service.append_activity_event(
            _graded("not-activity", "linear-core", at=START)
        )

    locks_before_scroll = len(_APPEND_LOCKS)
    for index in range(25):
        await service.append_activity_event(
            position.model_copy(update={"event_id": f"position-scroll-{index}"})
        )
    assert len(_APPEND_LOCKS) <= locks_before_scroll + 1


@pytest.mark.asyncio
async def test_review_queue_contains_only_current_version_due_items() -> None:
    due = _reduce(_mastery_events(), now=START + timedelta(days=2))
    future = due.model_copy(
        update={
            "chapter_key": "vectors",
            "concept_key": "vectors",
            "review_level": 2,
            "review_due_at": START + timedelta(days=5),
        }
    )
    blocked = due.model_copy(
        update={
            "chapter_key": "pending-transfer",
            "concept_key": "pending-transfer",
            "status": "practiced",
        }
    )

    async def current_version(course_id: str) -> str:
        assert course_id == "course:one"
        return "course_version:one"

    async def load_masteries(
        course_id: str, version_id: str
    ) -> tuple[ConceptMastery, ...]:
        assert (course_id, version_id) == (
            "course:one",
            "course_version:one",
        )
        return (future, blocked, due)

    queue = await LearningService(
        current_version_loader=current_version,
        mastery_loader=load_masteries,
    ).review_queue("course:one", START + timedelta(days=2))

    assert [(item.chapter_key, item.interval_days) for item in queue] == [("linear", 1)]
