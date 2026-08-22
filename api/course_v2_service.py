"""Thin Course V2 facade over deterministic assessment and learning services."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from api.course_service import CourseService
from api.models import (
    CourseExerciseGradeRequest,
    CourseExerciseGradeResponse,
    CourseLearningChapterOverview,
    CourseLearningEventRequest,
    CourseLearningEventResponse,
    CourseLearningOverviewResponse,
)
from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.learning_service import LearningService
from open_notebook.course.models import Chapter, CourseVersion
from open_notebook.course.v2_contracts import (
    ExerciseBlueprint,
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

        mastery_version, masteries = await CourseService.list_current_masteries(
            course_id
        )
        if self._record_id(mastery_version.id, "Mastery Course version") != version_id:
            raise InvalidInputError("Mastery snapshot uses a stale Course version.")
        now = self.clock()
        review_queue = await self.learning_service.review_queue(course_id, now)
        positions = await asyncio.gather(
            *(
                self.learning_service.latest_reading_position(
                    course_id, chapter.chapter_key
                )
                for chapter in chapters
            )
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

        event = LearningEvent(
            event_id=request.event_key,
            course_id=course_id,
            course_version_id=version_id,
            chapter_key=request.chapter_key,
            concept_key=request.concept_key,
            exercise_key=request.exercise_key,
            kind=request.kind,
            payload=request.payload,
            occurred_at=request.occurred_at,
        )
        if event.kind in {"chapter_opened", "reading_position"}:
            stored = await self.learning_service.append_activity_event(event)
            return CourseLearningEventResponse(event=stored, mastery=None)
        mastery = await self.learning_service.append_event(event)
        return CourseLearningEventResponse(event=event, mastery=mastery)

    async def list_exercises(
        self,
        course_id: str,
        chapter_key: str | None = None,
    ) -> tuple[ExerciseBlueprint, ...]:
        _version, exercises = await CourseService.list_current_exercises(
            course_id, chapter_key
        )
        return tuple(exercise.blueprint for exercise in exercises)

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

        grade = self.assessment_service.grade(exercise.blueprint, request.answer)
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
        event_key = f"grade-{uuid4().hex}"
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
        event = LearningEvent(
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
        mastery = await self.learning_service.append_event(event)
        return CourseExerciseGradeResponse(
            grade=grade,
            mastery=mastery,
            event_key=event_key,
        )


course_v2_service = CourseV2Service()


__all__ = ["CourseV2Service", "course_v2_service"]
