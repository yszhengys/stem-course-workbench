"""Replayable learning events, verified grading and deterministic review."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TypeAlias, cast
from weakref import WeakValueDictionary

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import InvalidInputError

from .assessment_service import AssessmentService
from .v2_contracts import (
    ConceptMastery,
    ExerciseBlueprint,
    GradedPayload,
    GraderSpec,
    HintViewedPayload,
    LearningEvent,
    ReviewCompletedPayload,
    ReviewQueueItem,
    TransferCompletedPayload,
    TransferTaskPayload,
)
from .v2_models import CourseConceptMastery, CourseExercise, CourseLearningEvent

REVIEW_INTERVAL_DAYS: tuple[int, ...] = (1, 3, 7, 14, 30)
MAX_FUTURE_SKEW = timedelta(minutes=5)
EventAppender: TypeAlias = Callable[[LearningEvent], Awaitable[None]]
EventLoader: TypeAlias = Callable[
    [str, str, str, str], Awaitable[tuple[LearningEvent, ...]]
]
ExerciseLoader: TypeAlias = Callable[
    [str, str, str], Awaitable[tuple[ExerciseBlueprint, ...]]
]
MasterySaver: TypeAlias = Callable[[ConceptMastery], Awaitable[None]]
MasteryLoader: TypeAlias = Callable[[str, str], Awaitable[tuple[ConceptMastery, ...]]]
CurrentVersionLoader: TypeAlias = Callable[[str], Awaitable[str]]
ScopeValidator: TypeAlias = Callable[[LearningEvent], Awaitable[None]]
Clock: TypeAlias = Callable[[], datetime]

_APPEND_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_EVENT_PRECEDENCE = {
    "chapter_opened": 0,
    "reading_position": 0,
    "hint_viewed": 10,
    "answer_revealed": 20,
    "transfer_required": 30,
    "graded_incorrect": 40,
    "graded_correct": 40,
    "transfer_completed": 50,
    "review_completed": 60,
}


@dataclass(slots=True)
class _AttemptState:
    exercise_key: str
    hints_used: int = 0
    answer_revealed: bool = False
    completed: bool = False


@dataclass(frozen=True, slots=True)
class _LearningScope:
    chapter_id: str
    outline_version_id: str | None
    outline_version_status: str | None
    uses_published_pointer: bool


@dataclass(slots=True)
class LearningService:
    """Own append-only events, pure replay and deterministic review scheduling."""

    event_appender: EventAppender | None = None
    event_loader: EventLoader | None = None
    exercise_loader: ExerciseLoader | None = None
    mastery_saver: MasterySaver | None = None
    mastery_loader: MasteryLoader | None = None
    current_version_loader: CurrentVersionLoader | None = None
    scope_validator: ScopeValidator | None = None
    clock: Clock = lambda: datetime.now(timezone.utc)

    @staticmethod
    def _require_aware(value: datetime, name: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must include a timezone")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _safe_add(value: datetime, days: int) -> datetime:
        try:
            return value + timedelta(days=days)
        except OverflowError as exc:
            raise ValueError("review schedule is outside the supported range") from exc

    @staticmethod
    def _attempt_key(event: LearningEvent) -> str:
        attempt_key = getattr(event.payload, "attempt_key", "")
        return attempt_key if isinstance(attempt_key, str) else ""

    @classmethod
    def _event_sort_key(
        cls, event: LearningEvent
    ) -> tuple[datetime, int, str, str, int, str]:
        hint_index = (
            event.payload.hint_index
            if isinstance(event.payload, HintViewedPayload)
            else 0
        )
        return (
            cls._require_aware(event.occurred_at, "occurred_at"),
            _EVENT_PRECEDENCE[event.kind],
            event.exercise_key or "",
            cls._attempt_key(event),
            hint_index,
            event.event_id,
        )

    @staticmethod
    def _exercise_catalog(
        exercises: Mapping[str, ExerciseBlueprint] | Iterable[ExerciseBlueprint],
        *,
        chapter_key: str,
    ) -> dict[str, ExerciseBlueprint]:
        values = exercises.values() if isinstance(exercises, Mapping) else exercises
        catalog: dict[str, ExerciseBlueprint] = {}
        for exercise in values:
            if not isinstance(exercise, ExerciseBlueprint):
                raise TypeError("exercise catalog must contain validated blueprints")
            if exercise.chapter_key != chapter_key:
                raise ValueError("exercise catalog contains a foreign chapter")
            if exercise.key in catalog and catalog[exercise.key] != exercise:
                raise ValueError("exercise catalog contains a duplicate key")
            catalog[exercise.key] = exercise
        if not catalog:
            raise ValueError("exercise catalog is empty")
        return catalog

    @staticmethod
    def _answer_for(
        exercise: ExerciseBlueprint,
        response_parts: tuple[str, ...],
    ) -> object:
        return AssessmentService.decode_response(exercise.grader, response_parts)

    @classmethod
    def reduce_events(
        cls,
        events: Iterable[LearningEvent],
        *,
        exercises: Mapping[str, ExerciseBlueprint] | Iterable[ExerciseBlueprint],
        now: datetime | None = None,
    ) -> ConceptMastery:
        """Replay one concept's immutable audit log into a canonical snapshot."""

        by_id: dict[str, LearningEvent] = {}
        for event in events:
            if not isinstance(event, LearningEvent):
                raise TypeError("events must contain validated LearningEvent values")
            existing = by_id.get(event.event_id)
            if existing is not None and existing != event:
                raise ValueError("conflicting duplicate learning event ID")
            by_id[event.event_id] = event
        if not by_id:
            raise ValueError("at least one concept learning event is required")
        ordered = tuple(sorted(by_id.values(), key=cls._event_sort_key))
        first = ordered[0]
        if first.concept_key is None:
            raise ValueError("concept_key is required for mastery reduction")
        identity = (
            first.course_id,
            first.course_version_id,
            first.chapter_key,
            first.concept_key,
        )
        if any(
            event.concept_key is None
            or (
                event.course_id,
                event.course_version_id,
                event.chapter_key,
                event.concept_key,
            )
            != identity
            for event in ordered
        ):
            raise ValueError("mastery events must share one exact concept identity")
        catalog = cls._exercise_catalog(exercises, chapter_key=identity[2])
        last_event_at = cls._require_aware(ordered[-1].occurred_at, "occurred_at")
        evaluation_time = (
            cls._require_aware(now, "now") if now is not None else last_event_at
        )
        if evaluation_time < last_event_at:
            raise ValueError("now cannot be before the last event")

        grading_inputs: list[tuple[GraderSpec, object]] = []
        for event in ordered:
            if event.exercise_key is None:
                continue
            grading_exercise = catalog.get(event.exercise_key)
            if grading_exercise is None:
                continue
            try:
                if event.kind in {"graded_correct", "graded_incorrect"}:
                    graded_payload = cast(GradedPayload, event.payload)
                    grading_inputs.append(
                        (
                            grading_exercise.grader,
                            cls._answer_for(
                                grading_exercise, graded_payload.response_parts
                            ),
                        )
                    )
                elif event.kind == "review_completed":
                    preflight_review = cast(ReviewCompletedPayload, event.payload)
                    grading_inputs.append(
                        (
                            grading_exercise.grader,
                            cls._answer_for(
                                grading_exercise, preflight_review.response_parts
                            ),
                        )
                    )
                elif event.kind == "transfer_completed":
                    preflight_transfer = cast(TransferCompletedPayload, event.payload)
                    if grading_exercise.transfer_task is not None:
                        grading_inputs.append(
                            (
                                grading_exercise.transfer_task.grader,
                                AssessmentService.decode_response(
                                    grading_exercise.transfer_task.grader,
                                    preflight_transfer.response_parts,
                                ),
                            )
                        )
            except (TypeError, ValueError):
                continue
        AssessmentService.prime_symbolic_grades(grading_inputs)

        successful_keys: set[str] = set()
        unrevealed_keys: set[str] = set()
        pending_transfers: set[tuple[str, str, str]] = set()
        required_transfers: set[tuple[str, str, str]] = set()
        completed_transfer_attempts: set[str] = set()
        attempts: dict[str, _AttemptState] = {}
        practiced = False
        mastered = False
        review_level = 0
        review_due_at: datetime | None = None

        for event in ordered:
            occurred_at = cls._require_aware(event.occurred_at, "occurred_at")
            exercise: ExerciseBlueprint | None = None
            if event.exercise_key is not None:
                exercise = catalog.get(event.exercise_key)
                if exercise is None:
                    raise ValueError("learning event references an unknown exercise")
                concept_keys = set(exercise.concept_keys)
                if exercise.transfer_task is not None:
                    concept_keys.update(exercise.transfer_task.invariant_concept_keys)
                if identity[3] not in concept_keys:
                    raise ValueError("learning event exercise has a foreign concept")

            if event.kind in {"chapter_opened", "reading_position"}:
                continue

            if exercise is None:
                raise ValueError("learning event requires a catalog exercise")
            attempt_key = cls._attempt_key(event)
            state: _AttemptState | None = None
            if event.kind in {
                "hint_viewed",
                "answer_revealed",
                "transfer_required",
                "graded_correct",
                "graded_incorrect",
                "review_completed",
            }:
                if not attempt_key:
                    raise ValueError("learning event requires an attempt key")
                if attempt_key in completed_transfer_attempts:
                    raise ValueError(
                        "a transfer and regular exercise cannot reuse an attempt identity"
                    )
                state = attempts.setdefault(
                    attempt_key, _AttemptState(exercise_key=exercise.key)
                )
                if state.exercise_key != exercise.key:
                    raise ValueError("an attempt cannot span multiple exercises")

            if event.kind == "hint_viewed":
                hint_payload = cast(HintViewedPayload, event.payload)
                if state is None or state.completed:
                    raise ValueError("hints cannot be viewed after attempt completion")
                if hint_payload.hint_index != state.hints_used + 1:
                    raise ValueError("hints must be viewed once in sequence")
                state.hints_used = hint_payload.hint_index

            elif event.kind == "answer_revealed":
                reveal_payload = cast(TransferTaskPayload, event.payload)
                if state is None or state.completed:
                    raise ValueError(
                        "answers cannot be revealed after attempt completion"
                    )
                if state.answer_revealed:
                    raise ValueError("an answer can be revealed only once per attempt")
                if exercise.is_core:
                    transfer = exercise.transfer_task
                    if (
                        transfer is None
                        or reveal_payload.transfer_task_key != transfer.key
                    ):
                        raise ValueError(
                            "a core answer reveal requires its verified transfer task"
                        )
                    pending_transfers.add(
                        (exercise.key, reveal_payload.attempt_key, transfer.key)
                    )
                elif reveal_payload.transfer_task_key is not None:
                    raise ValueError(
                        "a non-core answer reveal cannot declare a transfer task"
                    )
                state.answer_revealed = True

            elif event.kind == "transfer_required":
                required_payload = cast(TransferTaskPayload, event.payload)
                transfer = exercise.transfer_task
                if (
                    not exercise.is_core
                    or transfer is None
                    or required_payload.transfer_task_key != transfer.key
                ):
                    raise ValueError(
                        "transfer requirement does not match the core exercise"
                    )
                if state is None or not state.answer_revealed:
                    raise ValueError(
                        "transfer must be required by a prior answer reveal"
                    )
                requirement = (
                    exercise.key,
                    required_payload.attempt_key,
                    transfer.key,
                )
                if requirement not in pending_transfers:
                    raise ValueError(
                        "transfer must match its prior answer reveal attempt"
                    )
                required_transfers.add(requirement)

            elif event.kind in {"graded_correct", "graded_incorrect"}:
                graded_payload = cast(GradedPayload, event.payload)
                if state is None or state.completed:
                    raise ValueError("an attempt can be graded only once")
                if (
                    graded_payload.hints_used != state.hints_used
                    or graded_payload.answer_revealed != state.answer_revealed
                ):
                    raise ValueError("graded attempt summary does not match its events")
                answer = cls._answer_for(exercise, graded_payload.response_parts)
                grade = AssessmentService.grade(exercise, answer)
                if grade.advisory:
                    raise ValueError("advisory answers cannot create graded events")
                expected_correct = event.kind == "graded_correct"
                if grade.correct is not expected_correct:
                    raise ValueError(
                        "graded event does not match the server grade outcome"
                    )
                state.completed = True
                if grade.correct:
                    if exercise.is_core:
                        practiced = True
                    if (
                        exercise.is_core or exercise.is_source_level
                    ) and state.hints_used < 4:
                        successful_keys.add(exercise.key)
                        if not state.answer_revealed:
                            unrevealed_keys.add(exercise.key)

            elif event.kind == "transfer_completed":
                transfer_payload = cast(TransferCompletedPayload, event.payload)
                transfer = exercise.transfer_task
                requirement = (
                    exercise.key,
                    transfer_payload.source_attempt_key,
                    transfer_payload.transfer_task_key,
                )
                if (
                    not exercise.is_core
                    or transfer is None
                    or transfer_payload.transfer_task_key != transfer.key
                    or requirement not in pending_transfers
                    or requirement not in required_transfers
                ):
                    raise ValueError(
                        "transfer completion has no matching required transfer"
                    )
                if transfer_payload.attempt_key in attempts:
                    raise ValueError(
                        "a transfer and regular exercise cannot reuse an attempt identity"
                    )
                if transfer_payload.attempt_key in completed_transfer_attempts:
                    raise ValueError("a transfer attempt can be completed only once")
                answer = AssessmentService.decode_response(
                    transfer.grader, transfer_payload.response_parts
                )
                grade = AssessmentService.grade_transfer(transfer, answer)
                if grade.correct is not True or grade.advisory:
                    raise ValueError(
                        "transfer completion requires a correct server grade"
                    )
                completed_transfer_attempts.add(transfer_payload.attempt_key)
                pending_transfers.discard(requirement)
                required_transfers.discard(requirement)

            elif event.kind == "review_completed":
                review_payload = cast(ReviewCompletedPayload, event.payload)
                if state is None or state.completed:
                    raise ValueError("a review attempt can be completed only once")
                if (
                    review_payload.hints_used != state.hints_used
                    or review_payload.answer_revealed != state.answer_revealed
                ):
                    raise ValueError("review attempt summary does not match its events")
                answer = cls._answer_for(exercise, review_payload.response_parts)
                grade = AssessmentService.grade(exercise, answer)
                if grade.advisory or grade.correct is not review_payload.correct:
                    raise ValueError(
                        "review event does not match the server grade outcome"
                    )
                if not mastered or review_due_at is None:
                    raise ValueError("review is not due for this concept")
                would_advance = (
                    review_payload.correct
                    and not review_payload.answer_revealed
                    and state.hints_used < 4
                )
                if pending_transfers and would_advance:
                    raise ValueError("review cannot advance while transfer is pending")
                if occurred_at < review_due_at:
                    raise ValueError("review is not due yet")
                state.completed = True
                if not review_payload.correct:
                    review_level = 0
                    review_due_at = cls._safe_add(occurred_at, REVIEW_INTERVAL_DAYS[0])
                elif not review_payload.answer_revealed and state.hints_used < 4:
                    review_level = min(review_level + 1, 5)
                    review_due_at = cls._safe_add(
                        occurred_at,
                        REVIEW_INTERVAL_DAYS[min(review_level, 4)],
                    )

            mastery_ready = (
                practiced
                and len(successful_keys) >= 2
                and bool(unrevealed_keys)
                and not pending_transfers
            )
            if mastery_ready and not mastered:
                mastered = True
                review_level = 0
                review_due_at = cls._safe_add(occurred_at, REVIEW_INTERVAL_DAYS[0])

        if mastered and not pending_transfers:
            status = (
                "review_due"
                if review_due_at is not None and evaluation_time >= review_due_at
                else "mastered"
            )
        elif practiced:
            status = "practiced"
            if not mastered:
                review_due_at = None
                review_level = 0
        else:
            status = "learning"
            review_due_at = None
            review_level = 0

        successful = tuple(sorted(successful_keys))
        snapshot_payload = {
            "course_id": identity[0],
            "course_version_id": identity[1],
            "chapter_key": identity[2],
            "concept_key": identity[3],
            "status": status,
            "successful_exercise_keys": successful,
            "unrevealed_success_count": len(unrevealed_keys),
            "review_level": review_level,
            "review_due_at": (
                review_due_at.isoformat() if review_due_at is not None else None
            ),
            "last_event_at": last_event_at.isoformat(),
            "event_hashes": [
                hashlib.sha256(
                    json.dumps(
                        event.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                for event in ordered
            ],
        }
        snapshot_hash = hashlib.sha256(
            json.dumps(
                snapshot_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return ConceptMastery(
            course_id=identity[0],
            course_version_id=identity[1],
            chapter_key=identity[2],
            concept_key=identity[3],
            status=status,
            successful_exercise_keys=successful,
            unrevealed_success_count=len(unrevealed_keys),
            review_level=review_level,
            review_due_at=review_due_at,
            last_event_at=last_event_at,
            snapshot_hash=snapshot_hash,
        )

    async def append_event(self, event: LearningEvent) -> ConceptMastery:
        """Validate a server-authored event before one atomic audit commit."""

        if event.kind in {"chapter_opened", "reading_position"}:
            raise InvalidInputError("Activity events must use append_activity_event.")
        if event.concept_key is None:
            raise InvalidInputError(
                "A concept_key is required when appending a mastery event."
            )
        server_now = self._require_aware(self.clock(), "clock")
        occurred_at = self._require_aware(event.occurred_at, "occurred_at")
        if occurred_at > server_now + MAX_FUTURE_SKEW:
            raise InvalidInputError(
                "Learning event timestamp is too far in the future."
            )
        lock_key = "|".join(
            (
                event.course_id,
                event.course_version_id,
                event.chapter_key,
                event.concept_key,
            )
        )
        lock = _APPEND_LOCKS.setdefault(lock_key, asyncio.Lock())
        async with lock:
            custom_pipeline = any(
                value is not None
                for value in (
                    self.event_appender,
                    self.event_loader,
                    self.exercise_loader,
                    self.mastery_saver,
                )
            )
            scope: _LearningScope | None = None
            if self.scope_validator is not None:
                await self.scope_validator(event)
            elif not custom_pipeline:
                scope = await self._resolve_scope(event)

            loader = self.event_loader or self._load_event_records
            exercise_loader = self.exercise_loader or self._load_exercise_records
            existing = await loader(
                event.course_id,
                event.course_version_id,
                event.chapter_key,
                event.concept_key,
            )
            duplicate = next(
                (stored for stored in existing if stored.event_id == event.event_id),
                None,
            )
            if duplicate is not None and duplicate != event:
                raise InvalidInputError("Learning event ID already has other content.")
            if not custom_pipeline:
                global_duplicate = await self._load_event_by_key(
                    event.course_id, event.event_id
                )
                if global_duplicate is not None:
                    if global_duplicate != event:
                        raise InvalidInputError(
                            "Learning event ID already has other content."
                        )
                    duplicate = global_duplicate

            exercises = await exercise_loader(
                event.course_id,
                event.course_version_id,
                event.chapter_key,
            )
            candidate = existing if duplicate is not None else (*existing, event)
            evaluation_time = max(
                server_now,
                max(
                    self._require_aware(item.occurred_at, "occurred_at")
                    for item in candidate
                ),
            )
            try:
                mastery = await asyncio.to_thread(
                    self.reduce_events,
                    candidate,
                    exercises=exercises,
                    now=evaluation_time,
                )
            except (TypeError, ValueError) as exc:
                raise InvalidInputError(str(exc)) from exc

            if custom_pipeline:
                if duplicate is None:
                    appender = self.event_appender or self._append_event_record
                    await appender(event)
                replayed_events = await loader(
                    event.course_id,
                    event.course_version_id,
                    event.chapter_key,
                    event.concept_key,
                )
                try:
                    mastery = await asyncio.to_thread(
                        self.reduce_events,
                        replayed_events,
                        exercises=exercises,
                        now=evaluation_time,
                    )
                except (TypeError, ValueError) as exc:
                    raise InvalidInputError(str(exc)) from exc
                saver = self.mastery_saver or self._save_mastery_record
                await saver(mastery)
            else:
                if scope is None:
                    raise InvalidInputError("Learning event chapter was not found.")
                for commit_attempt in range(2):
                    try:
                        await self._commit_event_and_mastery(
                            event,
                            mastery,
                            scope=scope,
                            insert_event=duplicate is None,
                            expected_event_count=len(existing),
                        )
                        break
                    except RuntimeError:
                        if commit_attempt == 1:
                            raise
                        scope = await self._resolve_scope(event)
                        existing = await loader(
                            event.course_id,
                            event.course_version_id,
                            event.chapter_key,
                            event.concept_key,
                        )
                        duplicate = next(
                            (
                                stored
                                for stored in existing
                                if stored.event_id == event.event_id
                            ),
                            None,
                        )
                        if duplicate is not None and duplicate != event:
                            raise InvalidInputError(
                                "Learning event ID already has other content."
                            )
                        candidate = (
                            existing if duplicate is not None else (*existing, event)
                        )
                        try:
                            mastery = await asyncio.to_thread(
                                self.reduce_events,
                                candidate,
                                exercises=exercises,
                                now=max(
                                    evaluation_time,
                                    max(
                                        self._require_aware(
                                            item.occurred_at,
                                            "occurred_at",
                                        )
                                        for item in candidate
                                    ),
                                ),
                            )
                        except (TypeError, ValueError) as exc:
                            raise InvalidInputError(str(exc)) from exc
            return mastery

    async def append_reveal_events(
        self,
        revealed: LearningEvent,
        required: LearningEvent | None = None,
    ) -> ConceptMastery:
        """Commit an answer reveal and its transfer gate in one transaction."""

        events = (revealed,) if required is None else (revealed, required)
        if revealed.kind != "answer_revealed":
            raise InvalidInputError("The first reveal event must reveal an answer.")
        if required is not None and required.kind != "transfer_required":
            raise InvalidInputError("The second reveal event must require transfer.")
        revealed_payload = cast(TransferTaskPayload, revealed.payload)
        if (revealed_payload.transfer_task_key is None) is not (required is None):
            raise InvalidInputError(
                "A transfer-gated answer reveal must be committed with its gate."
            )
        identity = (
            revealed.course_id,
            revealed.course_version_id,
            revealed.chapter_key,
            revealed.concept_key,
            revealed.exercise_key,
        )
        if revealed.concept_key is None or revealed.exercise_key is None:
            raise InvalidInputError(
                "Answer reveals require one concept and exercise identity."
            )
        if any(
            (
                event.course_id,
                event.course_version_id,
                event.chapter_key,
                event.concept_key,
                event.exercise_key,
            )
            != identity
            for event in events
        ):
            raise InvalidInputError(
                "Atomic answer reveal events must share one learning identity."
            )
        if required is not None and cast(
            TransferTaskPayload, required.payload
        ) != revealed_payload:
            raise InvalidInputError(
                "The transfer gate must match its answer reveal payload."
            )
        server_now = self._require_aware(self.clock(), "clock")
        for event in events:
            occurred_at = self._require_aware(event.occurred_at, "occurred_at")
            if occurred_at > server_now + MAX_FUTURE_SKEW:
                raise InvalidInputError(
                    "Learning event timestamp is too far in the future."
                )
        if any(
            value is not None
            for value in (
                self.event_appender,
                self.event_loader,
                self.exercise_loader,
                self.mastery_saver,
                self.scope_validator,
            )
        ):
            raise InvalidInputError(
                "Atomic answer reveals require the database-backed learning pipeline."
            )

        lock_key = "|".join(
            (
                revealed.course_id,
                revealed.course_version_id,
                revealed.chapter_key,
                cast(str, revealed.concept_key),
            )
        )
        lock = _APPEND_LOCKS.setdefault(lock_key, asyncio.Lock())
        async with lock:
            exercises = await self._load_exercise_records(
                revealed.course_id,
                revealed.course_version_id,
                revealed.chapter_key,
            )

            async def prepare() -> tuple[
                tuple[LearningEvent, ...], tuple[bool, ...], ConceptMastery
            ]:
                existing = await self._load_event_records(
                    revealed.course_id,
                    revealed.course_version_id,
                    revealed.chapter_key,
                    cast(str, revealed.concept_key),
                )
                insert_events: list[bool] = []
                pending: list[LearningEvent] = []
                for event in events:
                    duplicate = next(
                        (
                            stored
                            for stored in existing
                            if stored.event_id == event.event_id
                        ),
                        None,
                    )
                    if duplicate is not None and duplicate != event:
                        raise InvalidInputError(
                            "Learning event ID already has other content."
                        )
                    global_duplicate = await self._load_event_by_key(
                        event.course_id, event.event_id
                    )
                    if global_duplicate is not None:
                        if global_duplicate != event:
                            raise InvalidInputError(
                                "Learning event ID already has other content."
                            )
                        duplicate = global_duplicate
                    should_insert = duplicate is None
                    insert_events.append(should_insert)
                    if should_insert:
                        pending.append(event)
                candidate = (*existing, *pending)
                evaluation_time = max(
                    server_now,
                    max(
                        self._require_aware(item.occurred_at, "occurred_at")
                        for item in candidate
                    ),
                )
                try:
                    mastery = await asyncio.to_thread(
                        self.reduce_events,
                        candidate,
                        exercises=exercises,
                        now=evaluation_time,
                    )
                except (TypeError, ValueError) as exc:
                    raise InvalidInputError(str(exc)) from exc
                return existing, tuple(insert_events), mastery

            scope = await self._resolve_scope(revealed)
            for commit_attempt in range(2):
                existing, insert_events, mastery = await prepare()
                try:
                    await self._commit_events_and_mastery(
                        events,
                        mastery,
                        scope=scope,
                        insert_events=insert_events,
                        expected_event_count=len(existing),
                    )
                    return mastery
                except RuntimeError:
                    if commit_attempt == 1:
                        raise
                    scope = await self._resolve_scope(revealed)
            raise RuntimeError("Atomic answer reveal commit did not complete.")

    async def append_activity_event(self, event: LearningEvent) -> LearningEvent:
        """Append chapter activity without creating a false mastery snapshot."""

        if event.kind not in {"chapter_opened", "reading_position"}:
            raise InvalidInputError("Only non-mastery activity events are accepted.")
        if event.concept_key is not None or event.exercise_key is not None:
            raise InvalidInputError(
                "Activity events cannot claim a concept or exercise."
            )
        server_now = self._require_aware(self.clock(), "clock")
        occurred_at = self._require_aware(event.occurred_at, "occurred_at")
        if occurred_at > server_now + MAX_FUTURE_SKEW:
            raise InvalidInputError(
                "Learning event timestamp is too far in the future."
            )

        lock_key = "|".join(
            (
                event.course_id,
                event.course_version_id,
                event.chapter_key,
                "activity",
            )
        )
        lock = _APPEND_LOCKS.setdefault(lock_key, asyncio.Lock())
        async with lock:
            if self.scope_validator is not None:
                await self.scope_validator(event)
                scope: _LearningScope | None = None
            elif self.event_appender is None:
                scope = await self._resolve_scope(event)
            else:
                scope = None

            existing = (
                await self._load_event_by_key(event.course_id, event.event_id)
                if self.event_appender is None
                else None
            )
            if existing is not None:
                if existing != event:
                    raise InvalidInputError(
                        "Learning event ID already has other content."
                    )
                return existing

            if self.event_appender is not None:
                await self.event_appender(event)
                return event
            if scope is None:
                raise InvalidInputError("Learning event chapter was not found.")

            try:
                await self._commit_activity_event(event, scope)
            except RuntimeError:
                await self._resolve_scope(event)
                concurrent = await self._load_event_by_key(
                    event.course_id, event.event_id
                )
                if concurrent == event:
                    return concurrent
                if concurrent is not None:
                    raise InvalidInputError(
                        "Learning event ID already has other content."
                    )
                raise
            return event

    async def latest_reading_position(
        self, course_id: str, chapter_key: str
    ) -> LearningEvent | None:
        """Restore the latest position from the current published version."""

        if not course_id.startswith("course:"):
            raise InvalidInputError("course_id must be a Course record ID.")
        version_id = await self._current_version_id(course_id)
        chapter_rows = await repo_query(
            """
            SELECT id FROM chapter
            WHERE course_version = $version AND chapter_key = $chapter_key
              AND status = 'published' LIMIT 1;
            """,
            {
                "version": ensure_record_id(version_id),
                "chapter_key": chapter_key,
            },
        )
        if not isinstance(chapter_rows, list) or not chapter_rows:
            raise InvalidInputError("Learning event chapter was not found.")
        rows = await repo_query(
            """
            SELECT * FROM course_learning_event
            WHERE course = $course AND course_version = $version
              AND chapter_key = $chapter_key AND kind = 'reading_position'
            ORDER BY occurred_at DESC, event_key DESC LIMIT 1;
            """,
            {
                "course": ensure_record_id(course_id),
                "version": ensure_record_id(version_id),
                "chapter_key": chapter_key,
            },
        )
        if not isinstance(rows, list) or not rows:
            return None
        return self._event_contract(CourseLearningEvent(**rows[0]))

    async def review_queue(
        self, course_id: str, now: datetime
    ) -> list[ReviewQueueItem]:
        if not course_id.startswith("course:"):
            raise InvalidInputError("course_id must be a Course record ID.")
        evaluation_time = self._require_aware(now, "now")
        version_loader = self.current_version_loader or self._current_version_id
        version_id = await version_loader(course_id)
        audited: list[ConceptMastery] = []
        if self.mastery_loader is not None:
            masteries = await self.mastery_loader(course_id, version_id)
            identities = tuple(
                (mastery.chapter_key, mastery.concept_key) for mastery in masteries
            )
            stored = {
                (mastery.chapter_key, mastery.concept_key): mastery
                for mastery in masteries
            }
        else:
            masteries = await self._load_mastery_records(course_id, version_id)
            stored = {
                (mastery.chapter_key, mastery.concept_key): mastery
                for mastery in masteries
            }
            identities = await self._load_event_identities(course_id, version_id)

        for chapter_key, concept_key in identities:
            mastery = stored.get((chapter_key, concept_key))
            if mastery is not None and (
                mastery.course_id != course_id
                or mastery.course_version_id != version_id
            ):
                raise InvalidInputError(
                    "Mastery loader returned a foreign Course version."
                )
            if self.mastery_loader is None:
                lock_key = "|".join((course_id, version_id, chapter_key, concept_key))
                lock = _APPEND_LOCKS.setdefault(lock_key, asyncio.Lock())
                async with lock:
                    for repair_attempt in range(2):
                        scope = await self._resolve_scope_values(
                            course_id, version_id, chapter_key
                        )
                        events = await self._load_event_records(
                            course_id,
                            version_id,
                            chapter_key,
                            concept_key,
                        )
                        if not events:
                            break
                        exercises = await self._load_exercise_records(
                            course_id, version_id, chapter_key
                        )
                        replay_time = max(
                            evaluation_time,
                            max(
                                self._require_aware(event.occurred_at, "occurred_at")
                                for event in events
                            ),
                        )
                        try:
                            replayed = await asyncio.to_thread(
                                self.reduce_events,
                                events,
                                exercises=exercises,
                                now=replay_time,
                            )
                        except (TypeError, ValueError) as exc:
                            raise InvalidInputError(str(exc)) from exc
                        if replayed == mastery:
                            mastery = replayed
                            break
                        try:
                            await self._save_mastery_if_event_count(
                                replayed,
                                scope=scope,
                                expected_event_count=len(events),
                            )
                            mastery = replayed
                            break
                        except RuntimeError:
                            if repair_attempt == 1:
                                raise
            if mastery is None:
                raise InvalidInputError("Mastery loader omitted a requested concept.")
            audited.append(mastery)

        if self.mastery_loader is None:
            for chapter_key, _concept_key in identities:
                await self._resolve_scope_values(course_id, version_id, chapter_key)

        items = [
            ReviewQueueItem(
                chapter_key=mastery.chapter_key,
                concept_key=mastery.concept_key,
                status="review_due",
                due_at=mastery.review_due_at,
                interval_days=cast(
                    int,
                    REVIEW_INTERVAL_DAYS[min(mastery.review_level, 4)],
                ),
            )
            for mastery in audited
            if mastery.status == "review_due"
            and mastery.review_due_at is not None
            and self._require_aware(mastery.review_due_at, "review_due_at")
            <= evaluation_time
        ]
        return sorted(
            items,
            key=lambda item: (
                item.due_at,
                item.chapter_key,
                item.concept_key,
            ),
        )

    @staticmethod
    def _event_contract(record: CourseLearningEvent) -> LearningEvent:
        return LearningEvent(
            event_id=record.event_key,
            course_id=record.course,
            course_version_id=record.course_version,
            chapter_key=record.chapter_key,
            concept_key=record.concept_key,
            exercise_key=record.exercise_key,
            kind=record.kind,
            payload=record.payload,
            occurred_at=record.occurred_at,
        )

    @staticmethod
    def _stable_record_id(table: str, *parts: str) -> str:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
        return f"{table}:{digest}"

    async def _resolve_scope(self, event: LearningEvent) -> _LearningScope:
        return await self._resolve_scope_values(
            event.course_id,
            event.course_version_id,
            event.chapter_key,
        )

    async def _resolve_scope_values(
        self,
        course_id: str,
        version_id: str,
        chapter_key: str,
    ) -> _LearningScope:
        course_rows = await repo_query(
            "SELECT outline_version_id FROM $course LIMIT 1;",
            {"course": ensure_record_id(course_id)},
        )
        if not isinstance(course_rows, list) or not course_rows:
            raise InvalidInputError("Course was not found.")
        current = course_rows[0].get("outline_version_id")
        outline_version_id = str(current) if current is not None else None
        outline_version_status: str | None = None
        uses_published_pointer = False
        current_version: str | None = None
        if outline_version_id is not None:
            pointed = await repo_query(
                "SELECT id, course, status FROM $version LIMIT 1;",
                {"version": ensure_record_id(outline_version_id)},
            )
            if not isinstance(pointed, list) or not pointed:
                raise InvalidInputError("Course current version was not found.")
            if str(pointed[0].get("course")) != course_id:
                raise InvalidInputError(
                    "Course current version does not belong to the Course."
                )
            raw_status = pointed[0].get("status")
            outline_version_status = str(raw_status) if raw_status is not None else None
            if outline_version_status == "published":
                current_version = outline_version_id
                uses_published_pointer = True
        if current_version is None:
            published = await repo_query(
                """
                SELECT id, version_no FROM course_version
                WHERE course = $course AND status = 'published'
                ORDER BY version_no DESC LIMIT 1;
                """,
                {"course": ensure_record_id(course_id)},
            )
            if not isinstance(published, list) or not published:
                raise InvalidInputError("Course has no current published version.")
            current_version = str(published[0]["id"])
        if current_version != version_id:
            raise InvalidInputError(
                "Learning events require the current published Course version."
            )
        chapter_rows = await repo_query(
            """
            SELECT id FROM chapter
            WHERE course_version = $version
              AND chapter_key = $chapter_key
              AND status = 'published'
            LIMIT 1;
            """,
            {
                "version": ensure_record_id(version_id),
                "chapter_key": chapter_key,
            },
        )
        if not isinstance(chapter_rows, list) or not chapter_rows:
            raise InvalidInputError("Learning event chapter was not found.")
        return _LearningScope(
            chapter_id=str(chapter_rows[0]["id"]),
            outline_version_id=outline_version_id,
            outline_version_status=outline_version_status,
            uses_published_pointer=uses_published_pointer,
        )

    async def _load_event_by_key(
        self, course_id: str, event_key: str
    ) -> LearningEvent | None:
        rows = await repo_query(
            """
            SELECT * FROM course_learning_event
            WHERE course = $course AND event_key = $event_key LIMIT 1;
            """,
            {
                "course": ensure_record_id(course_id),
                "event_key": event_key,
            },
        )
        if not isinstance(rows, list) or not rows:
            return None
        return self._event_contract(CourseLearningEvent(**rows[0]))

    async def _load_event_records(
        self,
        course_id: str,
        version_id: str,
        chapter_key: str,
        concept_key: str,
    ) -> tuple[LearningEvent, ...]:
        rows = await repo_query(
            """
            SELECT * FROM course_learning_event
            WHERE course = $course AND course_version = $version
              AND chapter_key = $chapter_key AND concept_key = $concept_key;
            """,
            {
                "course": ensure_record_id(course_id),
                "version": ensure_record_id(version_id),
                "chapter_key": chapter_key,
                "concept_key": concept_key,
            },
        )
        row_values = rows if isinstance(rows, list) else []
        return tuple(
            self._event_contract(CourseLearningEvent(**row))
            for row in row_values
            if isinstance(row, dict)
        )

    async def _load_event_identities(
        self, course_id: str, version_id: str
    ) -> tuple[tuple[str, str], ...]:
        rows = await repo_query(
            """
            SELECT chapter_key, concept_key FROM course_learning_event
            WHERE course = $course AND course_version = $version
              AND concept_key != NONE;
            """,
            {
                "course": ensure_record_id(course_id),
                "version": ensure_record_id(version_id),
            },
        )
        row_values = rows if isinstance(rows, list) else []
        identities = {
            (str(row["chapter_key"]), str(row["concept_key"]))
            for row in row_values
            if isinstance(row, dict)
            if row.get("chapter_key") is not None and row.get("concept_key") is not None
        }
        return tuple(sorted(identities))

    async def _load_exercise_records(
        self,
        course_id: str,
        version_id: str,
        chapter_key: str,
    ) -> tuple[ExerciseBlueprint, ...]:
        rows = await repo_query(
            """
            SELECT * FROM course_exercise
            WHERE course = $course AND course_version = $version
              AND chapter_key = $chapter_key;
            """,
            {
                "course": ensure_record_id(course_id),
                "version": ensure_record_id(version_id),
                "chapter_key": chapter_key,
            },
        )
        row_values = rows if isinstance(rows, list) else []
        records = tuple(
            CourseExercise(**row) for row in row_values if isinstance(row, dict)
        )
        if not records:
            raise InvalidInputError("No exercises were found for this Course chapter.")
        return tuple(record.blueprint for record in records)

    async def _append_event_record(self, event: LearningEvent) -> None:
        existing = await self._load_event_by_key(event.course_id, event.event_id)
        if existing is not None:
            if existing != event:
                raise InvalidInputError("Learning event ID already has other content.")
            return
        scope = await self._resolve_scope(event)
        record = CourseLearningEvent(
            course=event.course_id,
            course_version=event.course_version_id,
            chapter=scope.chapter_id,
            chapter_key=event.chapter_key,
            concept_key=event.concept_key,
            exercise_key=event.exercise_key,
            event_key=event.event_id,
            kind=event.kind,
            payload=event.payload,
            occurred_at=event.occurred_at,
        )
        await record.save()

    @staticmethod
    def _mastery_content(mastery: ConceptMastery) -> dict[str, object]:
        return {
            "course": ensure_record_id(mastery.course_id),
            "course_version": ensure_record_id(mastery.course_version_id),
            "chapter_key": mastery.chapter_key,
            "concept_key": mastery.concept_key,
            "status": mastery.status,
            "successful_exercise_keys": list(mastery.successful_exercise_keys),
            "unrevealed_success_count": mastery.unrevealed_success_count,
            "review_level": mastery.review_level,
            "review_due_at": mastery.review_due_at,
            "last_event_at": mastery.last_event_at,
            "snapshot_hash": mastery.snapshot_hash,
        }

    @staticmethod
    def _scope_guard_statement() -> str:
        return """
        LET $course_scope = (
            SELECT outline_version_id FROM $course LIMIT 1
        );
        IF array::len($course_scope) != 1 {
            THROW 'Learning event Course scope changed'
        };
        IF $course_scope[0].outline_version_id != $expected_outline_version {
            THROW 'Learning events require the current published Course version'
        };
        LET $version_scope = (
            SELECT VALUE id FROM $version
            WHERE course = $course AND status = 'published' LIMIT 1
        );
        IF array::len($version_scope) != 1 {
            THROW 'Learning events require the current published Course version'
        };
        LET $chapter_scope = (
            SELECT VALUE id FROM $chapter
            WHERE course_version = $version
              AND chapter_key = $chapter_key
              AND status = 'published'
            LIMIT 1
        );
        IF array::len($chapter_scope) != 1 {
            THROW 'Learning event chapter is no longer published'
        };
        IF $has_outline_version {
            LET $outline_scope = (
                SELECT VALUE id FROM $expected_outline_version
                WHERE course = $course
                  AND status = $expected_outline_status
                LIMIT 1
            );
            IF array::len($outline_scope) != 1 {
                THROW 'Learning events require the current published Course version'
            };
        };
        IF $uses_published_pointer {
            IF $expected_outline_version != $version {
                THROW 'Learning events require the current published Course version'
            };
        } ELSE {
            LET $latest_published = (
                SELECT id, version_no FROM course_version
                WHERE course = $course AND status = 'published'
                ORDER BY version_no DESC LIMIT 1
            );
            IF array::len($latest_published) != 1
               OR $latest_published[0].id != $version {
                THROW 'Learning events require the current published Course version'
            };
        };
        """

    @staticmethod
    def _scope_variables(
        event: LearningEvent, scope: _LearningScope
    ) -> dict[str, object]:
        return LearningService._scope_variables_for_identity(
            event.course_id,
            event.course_version_id,
            event.chapter_key,
            scope,
        )

    @staticmethod
    def _scope_variables_for_identity(
        course_id: str,
        version_id: str,
        chapter_key: str,
        scope: _LearningScope,
    ) -> dict[str, object]:
        return {
            "course": ensure_record_id(course_id),
            "version": ensure_record_id(version_id),
            "chapter": ensure_record_id(scope.chapter_id),
            "chapter_key": chapter_key,
            "expected_outline_version": (
                ensure_record_id(scope.outline_version_id)
                if scope.outline_version_id is not None
                else None
            ),
            "expected_outline_status": scope.outline_version_status,
            "has_outline_version": scope.outline_version_id is not None,
            "uses_published_pointer": scope.uses_published_pointer,
        }

    async def _commit_activity_event(
        self, event: LearningEvent, scope: _LearningScope
    ) -> None:
        statement = (
            "BEGIN TRANSACTION;"
            + self._scope_guard_statement()
            + """
            CREATE ONLY $record CONTENT $content;
            COMMIT TRANSACTION;
            """
        )
        variables = self._scope_variables(event, scope)
        variables.update(
            {
                "record": ensure_record_id(
                    self._stable_record_id(
                        "course_learning_event", event.course_id, event.event_id
                    )
                ),
                "content": {
                    "course": ensure_record_id(event.course_id),
                    "course_version": ensure_record_id(event.course_version_id),
                    "chapter": ensure_record_id(scope.chapter_id),
                    "chapter_key": event.chapter_key,
                    "concept_key": None,
                    "exercise_key": None,
                    "event_key": event.event_id,
                    "kind": event.kind,
                    "payload": event.payload.model_dump(mode="json"),
                    "occurred_at": event.occurred_at,
                },
            }
        )
        await repo_query(statement, variables)

    async def _commit_event_and_mastery(
        self,
        event: LearningEvent,
        mastery: ConceptMastery,
        *,
        scope: _LearningScope,
        insert_event: bool,
        expected_event_count: int,
    ) -> None:
        await self._commit_events_and_mastery(
            (event,),
            mastery,
            scope=scope,
            insert_events=(insert_event,),
            expected_event_count=expected_event_count,
        )

    async def _commit_events_and_mastery(
        self,
        events: tuple[LearningEvent, ...],
        mastery: ConceptMastery,
        *,
        scope: _LearningScope,
        insert_events: tuple[bool, ...],
        expected_event_count: int,
    ) -> None:
        if not events or len(events) != len(insert_events):
            raise ValueError("Atomic event commit inputs are inconsistent")
        def event_variable(prefix: str, index: int) -> str:
            return prefix if len(events) == 1 else f"{prefix}_{index}"

        event_statements = "".join(
            "CREATE ONLY "
            f"${event_variable('event_record', index)} CONTENT "
            f"${event_variable('event_content', index)};"
            for index, should_insert in enumerate(insert_events)
            if should_insert
        )
        statement = (
            "BEGIN TRANSACTION;"
            + self._scope_guard_statement()
            + """
        LET $current_events = (
            SELECT VALUE id FROM course_learning_event
            WHERE course = $course
              AND course_version = $version
              AND chapter_key = $chapter_key
              AND concept_key = $concept_key
        );
        IF array::len($current_events) != $expected_event_count {
            THROW 'Learning event snapshot changed'
        };
        """
            + event_statements
            + """
        UPSERT $mastery_record CONTENT $mastery_content;
        COMMIT TRANSACTION;
        """
        )
        variables = self._scope_variables(events[0], scope)
        variables.update(
            {
                "mastery_record": ensure_record_id(
                    self._stable_record_id(
                        "course_concept_mastery",
                        mastery.course_id,
                        mastery.course_version_id,
                        mastery.chapter_key,
                        mastery.concept_key,
                    )
                ),
                "mastery_content": self._mastery_content(mastery),
                "concept_key": events[0].concept_key,
                "expected_event_count": expected_event_count,
            }
        )
        for index, (event, should_insert) in enumerate(
            zip(events, insert_events, strict=True)
        ):
            if not should_insert:
                continue
            variables.update(
                {
                    event_variable("event_record", index): ensure_record_id(
                        self._stable_record_id(
                            "course_learning_event",
                            event.course_id,
                            event.event_id,
                        )
                    ),
                    event_variable("event_content", index): {
                        "course": ensure_record_id(event.course_id),
                        "course_version": ensure_record_id(event.course_version_id),
                        "chapter": ensure_record_id(scope.chapter_id),
                        "chapter_key": event.chapter_key,
                        "concept_key": event.concept_key,
                        "exercise_key": event.exercise_key,
                        "event_key": event.event_id,
                        "kind": event.kind,
                        "payload": event.payload.model_dump(mode="json"),
                        "occurred_at": event.occurred_at,
                    },
                }
            )
        await repo_query(statement, variables)

    async def _save_mastery_record(self, mastery: ConceptMastery) -> None:
        record_id = self._stable_record_id(
            "course_concept_mastery",
            mastery.course_id,
            mastery.course_version_id,
            mastery.chapter_key,
            mastery.concept_key,
        )
        await repo_query(
            "UPSERT $record CONTENT $content;",
            {
                "record": ensure_record_id(record_id),
                "content": self._mastery_content(mastery),
            },
        )

    async def _save_mastery_if_event_count(
        self,
        mastery: ConceptMastery,
        *,
        scope: _LearningScope,
        expected_event_count: int,
    ) -> None:
        statement = (
            "BEGIN TRANSACTION;"
            + self._scope_guard_statement()
            + """
            LET $current_events = (
                SELECT VALUE id FROM course_learning_event
                WHERE course = $course
                  AND course_version = $version
                  AND chapter_key = $chapter_key
                  AND concept_key = $concept_key
            );
            IF array::len($current_events) != $expected_event_count {
                THROW 'Learning event snapshot changed'
            };
            UPSERT $mastery_record CONTENT $mastery_content;
            COMMIT TRANSACTION;
            """
        )
        variables = self._scope_variables_for_identity(
            mastery.course_id,
            mastery.course_version_id,
            mastery.chapter_key,
            scope,
        )
        variables.update(
            {
                "mastery_record": ensure_record_id(
                    self._stable_record_id(
                        "course_concept_mastery",
                        mastery.course_id,
                        mastery.course_version_id,
                        mastery.chapter_key,
                        mastery.concept_key,
                    )
                ),
                "mastery_content": self._mastery_content(mastery),
                "concept_key": mastery.concept_key,
                "expected_event_count": expected_event_count,
            }
        )
        await repo_query(statement, variables)

    async def _current_version_id(self, course_id: str) -> str:
        course_rows = await repo_query(
            "SELECT outline_version_id FROM $course LIMIT 1;",
            {"course": ensure_record_id(course_id)},
        )
        if not isinstance(course_rows, list) or not course_rows:
            raise InvalidInputError("Course was not found.")
        current = course_rows[0].get("outline_version_id")
        if current is not None:
            version_id = str(current)
            pointed = await repo_query(
                """
                SELECT id, course, status FROM $version LIMIT 1;
                """,
                {"version": ensure_record_id(version_id)},
            )
            if not isinstance(pointed, list) or not pointed:
                raise InvalidInputError("Course current version was not found.")
            if str(pointed[0].get("course")) != course_id:
                raise InvalidInputError(
                    "Course current version does not belong to the Course."
                )
            if pointed[0].get("status") == "published":
                return version_id
        version_rows = await repo_query(
            """
            SELECT id, version_no FROM course_version
            WHERE course = $course AND status = 'published'
            ORDER BY version_no DESC LIMIT 1;
            """,
            {"course": ensure_record_id(course_id)},
        )
        if not isinstance(version_rows, list) or not version_rows:
            raise InvalidInputError("Course has no current published version.")
        return str(version_rows[0]["id"])

    async def _load_mastery_records(
        self, course_id: str, version_id: str
    ) -> tuple[ConceptMastery, ...]:
        rows = await repo_query(
            """
            SELECT * FROM course_concept_mastery
            WHERE course = $course AND course_version = $version;
            """,
            {
                "course": ensure_record_id(course_id),
                "version": ensure_record_id(version_id),
            },
        )
        row_values = rows if isinstance(rows, list) else []
        records = tuple(
            CourseConceptMastery(**row) for row in row_values if isinstance(row, dict)
        )
        return tuple(
            ConceptMastery(
                course_id=record.course,
                course_version_id=record.course_version,
                chapter_key=record.chapter_key,
                concept_key=record.concept_key,
                status=record.status,
                successful_exercise_keys=record.successful_exercise_keys,
                unrevealed_success_count=record.unrevealed_success_count,
                review_level=record.review_level,
                review_due_at=record.review_due_at,
                last_event_at=record.last_event_at,
                snapshot_hash=record.snapshot_hash,
            )
            for record in records
        )


__all__ = [
    "CurrentVersionLoader",
    "EventAppender",
    "EventLoader",
    "ExerciseLoader",
    "LearningService",
    "MAX_FUTURE_SKEW",
    "MasteryLoader",
    "MasterySaver",
    "REVIEW_INTERVAL_DAYS",
    "ScopeValidator",
]
