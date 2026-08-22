"""Thin Course V2 facade over deterministic assessment and learning services."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from api.course_service import CourseService
from api.models import (
    CourseExerciseGradeRequest,
    CourseExerciseGradeResponse,
    CourseExerciseResponse,
    CourseLearningChapterOverview,
    CourseLearningEventRequest,
    CourseLearningEventResponse,
    CourseLearningOverviewResponse,
    CourseTransferTaskResponse,
)
from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.learning_service import (
    REVIEW_INTERVAL_DAYS,
    LearningService,
)
from open_notebook.course.models import Chapter, CourseVersion
from open_notebook.course.v2_contracts import (
    GradedPayload,
    LearningEvent,
    ReviewCompletedPayload,
    ReviewQueueItem,
)
from open_notebook.course.v2_models import CourseExercise
from open_notebook.exceptions import InvalidInputError, OpenNotebookError


@dataclass
class CourseV2Service:
    """Resolve trusted record scope before invoking pure V2 domain logic."""

    learning_service: LearningService = field(default_factory=LearningService)
    assessment_service: type[AssessmentService] = AssessmentService
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    @staticmethod
    def _record_id(value: object, label: str) -> str:
        if value is None:
            raise OpenNotebookError(f"{label} has no identity")
        return str(value)

    @classmethod
    def _validate_scope(
        cls,
        course_id: str,
        version: CourseVersion,
        chapter: Chapter,
    ) -> tuple[str, str]:
        version_id = cls._record_id(version.id, "Published Course version")
        chapter_id = cls._record_id(chapter.id, "Published Course chapter")
        if version.course != course_id or version.status != "published":
            raise InvalidInputError("Version is outside the current Course scope.")
        if (
            chapter.course_version != version_id
            or chapter.status != "published"
        ):
            raise InvalidInputError("Chapter is outside the current Course scope.")
        return version_id, chapter_id

    @classmethod
    def _validate_exercise_scope(
        cls,
        exercise: CourseExercise,
        *,
        course_id: str,
        version_id: str,
        chapter: Chapter,
    ) -> None:
        chapter_id = cls._record_id(chapter.id, "Published Course chapter")
        if (
            exercise.course != course_id
            or exercise.course_version != version_id
            or exercise.chapter != chapter_id
            or exercise.chapter_key != chapter.chapter_key
        ):
            raise InvalidInputError(
                "Exercise is outside the current Course scope."
            )

    @staticmethod
    def _action_event_key(
        course_id: str,
        version_id: str,
        chapter_key: str,
        idempotency_key: str,
    ) -> str:
        digest = hashlib.sha256(
            "\x1f".join(
                (course_id, version_id, chapter_key, idempotency_key)
            ).encode("utf-8")
        ).hexdigest()
        return f"action-{digest}"

    @staticmethod
    def _same_action(
        existing: LearningEvent,
        *,
        course_id: str,
        version_id: str,
        request: CourseLearningEventRequest,
    ) -> bool:
        return (
            existing.course_id == course_id
            and existing.course_version_id == version_id
            and existing.chapter_key == request.chapter_key
            and existing.concept_key == request.concept_key
            and existing.exercise_key == request.exercise_key
            and existing.kind == request.kind
            and existing.payload == request.payload
        )

    @staticmethod
    def _grade_event_key(
        course_id: str,
        version_id: str,
        chapter_key: str,
        concept_key: str,
        exercise_key: str,
        attempt_key: str,
        mode: str,
    ) -> str:
        digest = hashlib.sha256(
            "\x1f".join(
                (
                    course_id,
                    version_id,
                    chapter_key,
                    concept_key,
                    exercise_key,
                    attempt_key,
                    mode,
                )
            ).encode("utf-8")
        ).hexdigest()
        return f"grade-{digest}"

    @staticmethod
    def _same_grade_event(
        existing: LearningEvent,
        candidate: LearningEvent,
    ) -> bool:
        return (
            existing.course_id == candidate.course_id
            and existing.course_version_id == candidate.course_version_id
            and existing.chapter_key == candidate.chapter_key
            and existing.concept_key == candidate.concept_key
            and existing.exercise_key == candidate.exercise_key
            and existing.kind == candidate.kind
            and existing.payload == candidate.payload
        )

    async def _scope(
        self, course_id: str, chapter_key: str
    ) -> tuple[CourseVersion, Chapter, str]:
        version, chapter = (
            await CourseService.resolve_current_published_chapter(
                course_id, chapter_key
            )
        )
        version_id, _chapter_id = self._validate_scope(
            course_id, version, chapter
        )
        return version, chapter, version_id

    async def get_learning_overview(
        self, course_id: str
    ) -> CourseLearningOverviewResponse:
        version, chapters = await CourseService.list_current_published_chapters(
            course_id
        )
        version_id = self._record_id(version.id, "Published Course version")
        if version.course != course_id or version.status != "published":
            raise InvalidInputError("Version is outside the current Course scope.")

        now = self.clock()
        review_queue = await self.learning_service.review_queue(course_id, now)
        mastery_version, masteries = await CourseService.list_current_masteries(
            course_id
        )
        if self._record_id(mastery_version.id, "Mastery Course version") != version_id:
            raise InvalidInputError("Mastery snapshot uses a stale Course version.")
        positions = await asyncio.gather(
            *(
                self.learning_service.latest_reading_position(
                    course_id, chapter.chapter_key
                )
                for chapter in chapters
            )
        )
        chapter_keys = {chapter.chapter_key for chapter in chapters}
        chapter_ids = {
            chapter.chapter_key: self._record_id(
                chapter.id, "Published Course chapter"
            )
            for chapter in chapters
        }
        if any(
            mastery.course_id != course_id
            or mastery.course_version_id != version_id
            or mastery.chapter_key not in chapter_keys
            for mastery in masteries
        ):
            raise InvalidInputError(
                "Mastery snapshot is outside the current Course chapter scope."
            )
        mastery_by_key = {
            (mastery.chapter_key, mastery.concept_key): mastery
            for mastery in masteries
        }
        queue_by_key = {
            (item.chapter_key, item.concept_key): item for item in review_queue
        }
        expected_due = {
            identity: mastery
            for identity, mastery in mastery_by_key.items()
            if mastery.status == "review_due"
            and mastery.review_due_at is not None
            and mastery.review_due_at <= now
        }
        if (
            len(queue_by_key) != len(review_queue)
            or set(queue_by_key) != set(expected_due)
        ):
            raise InvalidInputError(
                "Review queue does not match the current mastery snapshot."
            )
        for identity, item in queue_by_key.items():
            mastery = expected_due[identity]
            if (
                item.chapter_key not in chapter_keys
                or item.due_at != mastery.review_due_at
                or item.interval_days
                != REVIEW_INTERVAL_DAYS[min(mastery.review_level, 4)]
            ):
                raise InvalidInputError(
                    "Review queue does not match the current mastery snapshot."
                )
        for chapter, position in zip(chapters, positions, strict=True):
            if position is not None and (
                position.course_id != course_id
                or position.course_version_id != version_id
                or position.chapter_key != chapter.chapter_key
                or position.kind != "reading_position"
            ):
                raise InvalidInputError(
                    "Latest reading position uses a stale Course version."
                )
        await CourseService.confirm_current_published_scope(
            course_id,
            version_id,
            chapter_ids,
            exact=True,
        )
        return CourseLearningOverviewResponse(
            course_id=course_id,
            course_version_id=version_id,
            chapters=tuple(
                CourseLearningChapterOverview(
                    chapter_key=chapter.chapter_key,
                    chapter_no=chapter.chapter_no,
                    title=chapter.title,
                    latest_position=position,
                )
                for chapter, position in zip(chapters, positions, strict=True)
            ),
            masteries=masteries,
            review_queue=tuple(review_queue),
        )

    async def get_review_queue(
        self,
        course_id: str,
    ) -> tuple[ReviewQueueItem, ...]:
        await CourseService.get_current_published_version(course_id)
        return tuple(
            await self.learning_service.review_queue(course_id, self.clock())
        )

    async def append_learning_event(
        self,
        course_id: str,
        request: CourseLearningEventRequest,
    ) -> CourseLearningEventResponse:
        _version, chapter, version_id = await self._scope(
            course_id, request.chapter_key
        )
        if request.exercise_key is not None:
            exercise = await CourseService.get_current_exercise(
                course_id,
                request.chapter_key,
                request.exercise_key,
            )
            self._validate_exercise_scope(
                exercise,
                course_id=course_id,
                version_id=version_id,
                chapter=chapter,
            )
            concepts = set(exercise.blueprint.concept_keys)
            if exercise.blueprint.transfer_task is not None:
                concepts.update(
                    exercise.blueprint.transfer_task.invariant_concept_keys
                )
            if request.concept_key not in concepts:
                raise InvalidInputError(
                    "Exercise does not cover the requested concept stable key."
                )

        event_key = self._action_event_key(
            course_id,
            version_id,
            request.chapter_key,
            request.idempotency_key,
        )
        existing = await CourseService.get_learning_event(course_id, event_key)
        if existing is not None:
            if not self._same_action(
                existing,
                course_id=course_id,
                version_id=version_id,
                request=request,
            ):
                raise InvalidInputError(
                    "Idempotency key was already used for another learning action."
                )
            event = existing
        else:
            event = LearningEvent(
                event_id=event_key,
                course_id=course_id,
                course_version_id=version_id,
                chapter_key=request.chapter_key,
                concept_key=request.concept_key,
                exercise_key=request.exercise_key,
                kind=request.kind,
                payload=request.payload,
                occurred_at=self.clock(),
            )

        try:
            if event.kind in {"chapter_opened", "reading_position"}:
                stored = await self.learning_service.append_activity_event(event)
                return CourseLearningEventResponse(event=stored, mastery=None)
            mastery = await self.learning_service.append_event(event)
            return CourseLearningEventResponse(event=event, mastery=mastery)
        except InvalidInputError:
            concurrent = await CourseService.get_learning_event(
                course_id, event_key
            )
            if concurrent is None or not self._same_action(
                concurrent,
                course_id=course_id,
                version_id=version_id,
                request=request,
            ):
                raise
            if concurrent.kind in {"chapter_opened", "reading_position"}:
                stored = await self.learning_service.append_activity_event(concurrent)
                return CourseLearningEventResponse(event=stored, mastery=None)
            mastery = await self.learning_service.append_event(concurrent)
            return CourseLearningEventResponse(event=concurrent, mastery=mastery)

    async def list_exercises(
        self,
        course_id: str,
        chapter_key: str | None = None,
    ) -> tuple[CourseExerciseResponse, ...]:
        _version, exercises = await CourseService.list_current_exercises(
            course_id, chapter_key
        )
        return tuple(
            CourseExerciseResponse(
                key=exercise.blueprint.key,
                chapter_key=exercise.blueprint.chapter_key,
                prompt=exercise.blueprint.prompt,
                concept_keys=exercise.blueprint.concept_keys,
                exercise_type=exercise.blueprint.exercise_type,
                answer_type=exercise.blueprint.answer_type,
                source_anchor_ids=exercise.blueprint.source_anchor_ids,
                source_number=exercise.blueprint.source_number,
                source_section=exercise.blueprint.source_section,
                difficulty=exercise.blueprint.difficulty,
                is_core=exercise.blueprint.is_core,
                is_gating=exercise.blueprint.is_gating,
                is_source_level=exercise.blueprint.is_source_level,
                transfer=(
                    CourseTransferTaskResponse(
                        key=exercise.blueprint.transfer_task.key,
                        prompt=exercise.blueprint.transfer_task.prompt,
                        invariant_concept_keys=(
                            exercise.blueprint.transfer_task.invariant_concept_keys
                        ),
                        dimensions=exercise.blueprint.transfer_task.dimensions,
                        answer_type=exercise.blueprint.transfer_task.answer_type,
                        difficulty=exercise.blueprint.transfer_task.difficulty,
                        anchor_ids=exercise.blueprint.transfer_task.anchor_ids,
                    )
                    if exercise.blueprint.transfer_task is not None
                    else None
                ),
            )
            for exercise in exercises
        )

    async def grade_exercise(
        self,
        course_id: str,
        exercise_key: str,
        request: CourseExerciseGradeRequest,
    ) -> CourseExerciseGradeResponse:
        _version, chapter, version_id = await self._scope(
            course_id, request.chapter_key
        )
        exercise = await CourseService.get_current_exercise(
            course_id,
            request.chapter_key,
            exercise_key,
        )
        self._validate_exercise_scope(
            exercise,
            course_id=course_id,
            version_id=version_id,
            chapter=chapter,
        )
        if exercise.exercise_key != exercise_key:
            raise InvalidInputError("Exercise stable key does not match the request.")
        if request.concept_key not in exercise.blueprint.concept_keys:
            raise InvalidInputError(
                "Exercise does not cover the requested concept stable key."
            )

        grade = await asyncio.to_thread(
            self.assessment_service.grade,
            exercise.blueprint,
            request.answer,
        )
        if grade.advisory:
            return CourseExerciseGradeResponse(
                grade=grade,
                mastery=None,
                event_key=None,
            )

        response_parts = (
            json.dumps(
                request.answer,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        event_key = self._grade_event_key(
            course_id,
            version_id,
            request.chapter_key,
            request.concept_key,
            exercise_key,
            request.attempt_key,
            request.mode,
        )
        payload: ReviewCompletedPayload | GradedPayload
        if request.mode == "review":
            payload = ReviewCompletedPayload(
                attempt_key=request.attempt_key,
                correct=grade.correct is True,
                answer_revealed=request.answer_revealed,
                hints_used=request.hints_used,
                response_parts=response_parts,
            )
            kind = "review_completed"
        else:
            payload = GradedPayload(
                attempt_key=request.attempt_key,
                answer_revealed=request.answer_revealed,
                hints_used=request.hints_used,
                response_parts=response_parts,
            )
            kind = "graded_correct" if grade.correct is True else "graded_incorrect"
        candidate = LearningEvent(
            event_id=event_key,
            course_id=course_id,
            course_version_id=version_id,
            chapter_key=request.chapter_key,
            concept_key=request.concept_key,
            exercise_key=exercise_key,
            kind=kind,
            payload=payload,
            occurred_at=self.clock(),
        )
        existing = await CourseService.get_learning_event(course_id, event_key)
        if existing is not None:
            if not self._same_grade_event(existing, candidate):
                raise InvalidInputError(
                    "Attempt key was already graded with different content."
                )
            event = existing
        else:
            event = candidate
        try:
            mastery = await self.learning_service.append_event(event)
        except InvalidInputError:
            concurrent = await CourseService.get_learning_event(
                course_id, event_key
            )
            if concurrent is None or not self._same_grade_event(
                concurrent, candidate
            ):
                raise
            event = concurrent
            mastery = await self.learning_service.append_event(event)
        return CourseExerciseGradeResponse(
            grade=grade,
            mastery=mastery,
            event_key=event_key,
        )


course_v2_service = CourseV2Service()


__all__ = ["CourseV2Service", "course_v2_service"]
