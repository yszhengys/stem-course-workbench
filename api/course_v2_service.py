"""Thin Course V2 facade over deterministic assessment and learning services."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from api.course_service import CourseConflictError, CourseService
from api.models import (
    CourseActivityEventRequest,
    CourseAnswerFormat,
    CourseConceptResponse,
    CourseExerciseGradeRequest,
    CourseExerciseGradeResponse,
    CourseExerciseHintRequest,
    CourseExerciseHintResponse,
    CourseExerciseResponse,
    CourseExerciseRevealRequest,
    CourseExerciseRevealResponse,
    CourseLearnerChapterArtifact,
    CourseLearnerChapterResponse,
    CourseLearnerChapterSection,
    CourseLearnerFormula,
    CourseLearnerNoteCreateRequest,
    CourseLearnerNoteResponse,
    CourseLearnerNotesResponse,
    CourseLearnerSourceResponse,
    CourseLearnerSourcesResponse,
    CourseLearnerWorkedExample,
    CourseLearningChapterOverview,
    CourseLearningEventRequest,
    CourseLearningEventResponse,
    CourseLearningOverviewResponse,
    CourseTransferGradeRequest,
    CourseTransferTaskResponse,
)
from open_notebook.course.assessment_service import AssessmentService
from open_notebook.course.contracts import ChapterArtifact, CourseOutlineArtifact
from open_notebook.course.learning_service import (
    REVIEW_INTERVAL_DAYS,
    LearningService,
)
from open_notebook.course.models import Chapter, CourseNote, CourseVersion
from open_notebook.course.v2_contracts import (
    AdvisoryGraderSpec,
    AnswerType,
    GradedPayload,
    GraderSpec,
    LearningEvent,
    MultipartGraderSpec,
    NumericGraderSpec,
    ReviewCompletedPayload,
    ReviewQueueItem,
    SetGraderSpec,
    SymbolicGraderSpec,
    TransferCompletedPayload,
    TransferTaskSpec,
    UnitGraderSpec,
    VectorGraderSpec,
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

    @staticmethod
    def _canonical_key(value: str, prefix: str) -> str:
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", value):
            return value
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
        return f"{prefix}-{digest}"

    @classmethod
    def _chapter_snapshot_token(
        cls,
        course_id: str,
        version: CourseVersion,
        chapter: Chapter,
    ) -> str:
        payload = {
            "course_id": course_id,
            "course_version_id": cls._record_id(version.id, "Course version"),
            "version_outline_hash": version.outline_hash,
            "version_input_hash": version.input_hash,
            "chapter_id": cls._record_id(chapter.id, "Course chapter"),
            "chapter_input_hash": chapter.input_hash,
            "artifact": chapter.artifact,
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _exercise_snapshot_token(
        cls,
        course_id: str,
        version: CourseVersion,
        exercise: CourseExercise,
    ) -> str:
        payload = {
            "course_id": course_id,
            "course_version_id": cls._record_id(version.id, "Course version"),
            "version_outline_hash": version.outline_hash,
            "version_input_hash": version.input_hash,
            "chapter_id": exercise.chapter,
            "exercise": exercise.blueprint.model_dump(mode="json"),
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _require_snapshot(expected: str, received: str) -> None:
        if not secrets.compare_digest(expected, received):
            raise CourseConflictError(
                "The published learning snapshot changed; reload before continuing."
            )

    @classmethod
    def _answer_format(
        cls,
        grader: GraderSpec,
        declared_kind: AnswerType | None = None,
    ) -> CourseAnswerFormat:
        if isinstance(grader, VectorGraderSpec):
            return CourseAnswerFormat(
                kind="vector",
                component_count=len(grader.expected_components),
                unit_required=grader.expected_unit is not None,
            )
        if isinstance(grader, MultipartGraderSpec):
            return CourseAnswerFormat(
                kind="multipart",
                parts=tuple(cls._answer_format(part) for part in grader.parts),
            )
        if isinstance(grader, UnitGraderSpec):
            return CourseAnswerFormat(kind="unit", unit_required=True)
        if isinstance(grader, NumericGraderSpec):
            return CourseAnswerFormat(kind="numeric")
        if isinstance(grader, SymbolicGraderSpec):
            return CourseAnswerFormat(kind="symbolic")
        if isinstance(grader, SetGraderSpec):
            return CourseAnswerFormat(kind="set")
        if isinstance(grader, AdvisoryGraderSpec):
            return CourseAnswerFormat(
                kind=(
                    declared_kind
                    if declared_kind in {"proof", "explanation"}
                    else "explanation"
                )
            )
        raise InvalidInputError("Exercise uses an unsupported answer format.")

    @classmethod
    def _learner_artifact(
        cls,
        chapter: Chapter,
        chapter_key: str,
    ) -> CourseLearnerChapterArtifact:
        try:
            artifact = ChapterArtifact.model_validate(chapter.artifact)
        except Exception as exc:
            raise InvalidInputError(
                "Published chapter content is unavailable or invalid."
            ) from exc
        if artifact.chapter_key != chapter_key:
            raise InvalidInputError(
                "Published chapter artifact uses another stable key."
            )
        return CourseLearnerChapterArtifact(
            purpose=artifact.purpose,
            prerequisites=tuple(artifact.prerequisites),
            objectives=tuple(artifact.objectives),
            sections=tuple(
                CourseLearnerChapterSection(
                    block_key=cls._canonical_key(section.key, "block"),
                    title=section.title,
                    markdown=section.markdown,
                    anchor_ids=tuple(section.anchor_ids),
                    provenance=section.provenance,
                )
                for section in artifact.sections
            ),
            definitions=tuple(artifact.definitions),
            formulas=tuple(
                CourseLearnerFormula(
                    key=cls._canonical_key(formula.key, "formula"),
                    latex=formula.latex,
                    meaning=formula.meaning,
                    anchor_ids=tuple(formula.anchor_ids),
                    unit_expression=formula.unit_expression,
                    provenance=formula.provenance,
                )
                for formula in artifact.formulas
            ),
            worked_examples=tuple(
                CourseLearnerWorkedExample(
                    key=cls._canonical_key(example.key, "example"),
                    prompt=example.prompt,
                    steps=tuple(example.steps),
                    answer=example.answer,
                    anchor_ids=tuple(example.anchor_ids),
                    unit_expression=example.unit_expression,
                    provenance=example.provenance,
                )
                for example in artifact.worked_examples
            ),
            misconceptions=tuple(artifact.misconceptions),
            pitfalls=tuple(artifact.pitfalls),
            quick_reference=tuple(artifact.quick_reference),
            citations=tuple(artifact.citations),
        )

    @staticmethod
    def _learner_anchor_ids(
        artifact: CourseLearnerChapterArtifact,
    ) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            anchor_id
            for anchors in (
                artifact.citations,
                *(section.anchor_ids for section in artifact.sections),
                *(formula.anchor_ids for formula in artifact.formulas),
                *(example.anchor_ids for example in artifact.worked_examples),
            )
            for anchor_id in anchors
        ))

    @staticmethod
    def _learner_block_keys(
        artifact: CourseLearnerChapterArtifact,
    ) -> frozenset[str]:
        return frozenset(
            [section.block_key for section in artifact.sections]
            + [formula.key for formula in artifact.formulas]
            + [example.key for example in artifact.worked_examples]
        )

    @classmethod
    def _transfer_response(
        cls, transfer: TransferTaskSpec
    ) -> CourseTransferTaskResponse:
        return CourseTransferTaskResponse(
            key=transfer.key,
            prompt=transfer.prompt,
            invariant_concept_keys=transfer.invariant_concept_keys,
            dimensions=transfer.dimensions,
            answer_type=transfer.answer_type,
            answer_format=cls._answer_format(
                transfer.grader, transfer.answer_type
            ),
            difficulty=transfer.difficulty,
            anchor_ids=transfer.anchor_ids,
        )

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

    async def _event_for_action(
        self,
        course_id: str,
        version_id: str,
        request: CourseLearningEventRequest,
        *,
        occurred_at: datetime | None = None,
    ) -> LearningEvent:
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
            return existing
        return LearningEvent(
            event_id=event_key,
            course_id=course_id,
            course_version_id=version_id,
            chapter_key=request.chapter_key,
            concept_key=request.concept_key,
            exercise_key=request.exercise_key,
            kind=request.kind,
            payload=request.payload,
            occurred_at=occurred_at or self.clock(),
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

    async def _exercise_scope(
        self,
        course_id: str,
        chapter_key: str,
        exercise_key: str,
        snapshot_token: str,
    ) -> tuple[CourseVersion, Chapter, str, CourseExercise, str]:
        version, chapter, version_id = await self._scope(course_id, chapter_key)
        exercise = await CourseService.get_current_exercise(
            course_id, chapter_key, exercise_key
        )
        self._validate_exercise_scope(
            exercise,
            course_id=course_id,
            version_id=version_id,
            chapter=chapter,
        )
        if exercise.exercise_key != exercise_key:
            raise InvalidInputError("Exercise stable key does not match the request.")
        current_snapshot = self._exercise_snapshot_token(
            course_id, version, exercise
        )
        self._require_snapshot(current_snapshot, snapshot_token)
        return version, chapter, version_id, exercise, current_snapshot

    async def get_learning_chapter(
        self,
        course_id: str,
        chapter_key: str,
    ) -> CourseLearnerChapterResponse:
        version, chapter, version_id = await self._scope(course_id, chapter_key)
        learner_artifact = self._learner_artifact(chapter, chapter_key)
        return CourseLearnerChapterResponse(
            course_id=course_id,
            course_version_id=version_id,
            chapter_key=chapter_key,
            chapter_no=chapter.chapter_no,
            title=chapter.title,
            status="published",
            snapshot_token=self._chapter_snapshot_token(
                course_id, version, chapter
            ),
            artifact=learner_artifact,
        )

    async def list_learning_sources(
        self,
        course_id: str,
        chapter_key: str,
    ) -> CourseLearnerSourcesResponse:
        version, chapter, version_id = await self._scope(course_id, chapter_key)
        learner_artifact = self._learner_artifact(chapter, chapter_key)
        sources: list[CourseLearnerSourceResponse] = []
        for anchor_id in self._learner_anchor_ids(learner_artifact):
            anchor, _evidence, source, _source_hash = (
                await CourseService._owned_evidence_asset(course_id, anchor_id)
            )
            sources.append(CourseLearnerSourceResponse(
                anchor_id=anchor.anchor_id,
                filename=source.filename,
                kind=anchor.locator.kind,
                index=anchor.locator.index,
                quote=anchor.locator.quote,
                source_role=anchor.source_role,
                bbox=anchor.locator.bbox,
            ))
        chapter_id = self._record_id(chapter.id, "Published Course chapter")
        await CourseService.confirm_current_published_scope(
            course_id,
            version_id,
            {chapter_key: chapter_id},
            exact=False,
        )
        return CourseLearnerSourcesResponse(
            snapshot_token=self._chapter_snapshot_token(
                course_id, version, chapter
            ),
            sources=tuple(sources),
        )

    @classmethod
    def _learner_note_response(cls, note: CourseNote) -> CourseLearnerNoteResponse:
        return CourseLearnerNoteResponse(
            note_id=cls._record_id(note.id, "Course note"),
            block_key=note.block_key or "unattached",
            content=note.content,
            orphan_status=(
                "orphaned" if note.orphan_status == "orphaned" else "active"
            ),
            created=note.created,
        )

    async def list_learning_notes(
        self,
        course_id: str,
        chapter_key: str,
    ) -> CourseLearnerNotesResponse:
        version, chapter, version_id = await self._scope(course_id, chapter_key)
        chapter_id = self._record_id(chapter.id, "Published Course chapter")
        notes = tuple(
            self._learner_note_response(note)
            for note in await CourseNote.list_by_course(course_id)
            if note.chapter == chapter_id
            and note.chapter_key == chapter_key
            and note.block_key is not None
        )
        await CourseService.confirm_current_published_scope(
            course_id,
            version_id,
            {chapter_key: chapter_id},
            exact=False,
        )
        return CourseLearnerNotesResponse(
            snapshot_token=self._chapter_snapshot_token(
                course_id, version, chapter
            ),
            notes=notes,
        )

    async def create_learning_note(
        self,
        course_id: str,
        chapter_key: str,
        request: CourseLearnerNoteCreateRequest,
    ) -> CourseLearnerNoteResponse:
        version, chapter, version_id = await self._scope(course_id, chapter_key)
        snapshot = self._chapter_snapshot_token(course_id, version, chapter)
        self._require_snapshot(snapshot, request.snapshot_token)
        learner_artifact = self._learner_artifact(chapter, chapter_key)
        if request.block_key not in self._learner_block_keys(learner_artifact):
            raise InvalidInputError(
                "Course note block is outside the published learner chapter."
            )
        chapter_id = self._record_id(chapter.id, "Published Course chapter")
        note = CourseNote(
            course=course_id,
            chapter=chapter_id,
            chapter_key=chapter_key,
            block_key=request.block_key,
            orphan_status="active",
            content=request.content,
        )
        await note.save()
        try:
            await CourseService.confirm_current_published_scope(
                course_id,
                version_id,
                {chapter_key: chapter_id},
                exact=False,
            )
        except Exception:
            await note.delete()
            raise
        return self._learner_note_response(note)

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
        concepts: tuple[CourseConceptResponse, ...] = ()
        if version.outline_artifact is not None:
            try:
                outline = CourseOutlineArtifact.model_validate(
                    version.outline_artifact
                )
            except Exception as exc:
                raise InvalidInputError(
                    "Published Course outline is invalid."
                ) from exc
            concepts = tuple(
                CourseConceptResponse(key=concept.key, label=concept.label)
                for concept in outline.concepts
            )
        return CourseLearningOverviewResponse(
            course_id=course_id,
            course_version_id=version_id,
            chapters=tuple(
                CourseLearningChapterOverview(
                    chapter_key=chapter.chapter_key,
                    chapter_no=chapter.chapter_no,
                    title=chapter.title,
                    snapshot_token=self._chapter_snapshot_token(
                        course_id, version, chapter
                    ),
                    latest_position=position,
                )
                for chapter, position in zip(chapters, positions, strict=True)
            ),
            concepts=concepts,
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
        version, chapter, version_id = await self._scope(
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
            expected_snapshot = self._exercise_snapshot_token(
                course_id, version, exercise
            )
        else:
            expected_snapshot = self._chapter_snapshot_token(
                course_id, version, chapter
            )
        self._require_snapshot(expected_snapshot, request.snapshot_token)

        event = await self._event_for_action(course_id, version_id, request)
        event_key = event.event_id

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

    async def append_activity_event(
        self,
        course_id: str,
        request: CourseActivityEventRequest,
    ) -> CourseLearningEventResponse:
        return await self.append_learning_event(
            course_id,
            CourseLearningEventRequest(
                snapshot_token=request.snapshot_token,
                idempotency_key=request.idempotency_key,
                chapter_key=request.chapter_key,
                kind=request.kind,
                payload=request.payload,
            ),
        )

    async def list_exercises(
        self,
        course_id: str,
        chapter_key: str | None = None,
    ) -> tuple[CourseExerciseResponse, ...]:
        version, exercises = await CourseService.list_current_exercises(
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
                answer_format=self._answer_format(
                    exercise.blueprint.grader,
                    exercise.blueprint.answer_type,
                ),
                snapshot_token=self._exercise_snapshot_token(
                    course_id, version, exercise
                ),
                source_anchor_ids=exercise.blueprint.source_anchor_ids,
                source_number=exercise.blueprint.source_number,
                source_section=exercise.blueprint.source_section,
                difficulty=exercise.blueprint.difficulty,
                is_core=exercise.blueprint.is_core,
                is_gating=exercise.blueprint.is_gating,
                is_source_level=exercise.blueprint.is_source_level,
                transfer=(
                    self._transfer_response(exercise.blueprint.transfer_task)
                    if exercise.blueprint.transfer_task is not None
                    else None
                ),
            )
            for exercise in exercises
        )

    async def next_hint(
        self,
        course_id: str,
        exercise_key: str,
        request: CourseExerciseHintRequest,
    ) -> CourseExerciseHintResponse:
        _version, _chapter, _version_id, exercise, snapshot = (
            await self._exercise_scope(
                course_id,
                request.chapter_key,
                exercise_key,
                request.snapshot_token,
            )
        )
        if request.concept_key not in exercise.blueprint.concept_keys:
            raise InvalidInputError(
                "Exercise does not cover the requested concept stable key."
            )
        hints = exercise.blueprint.hints
        if request.hint_index > len(hints):
            raise InvalidInputError("Requested hint layer is unavailable.")
        result = await self.append_learning_event(
            course_id,
            CourseLearningEventRequest(
                snapshot_token=snapshot,
                idempotency_key=request.idempotency_key,
                chapter_key=request.chapter_key,
                concept_key=request.concept_key,
                exercise_key=exercise_key,
                kind="hint_viewed",
                payload={
                    "attempt_key": request.attempt_key,
                    "hint_index": request.hint_index,
                },
            ),
        )
        return CourseExerciseHintResponse(
            snapshot_token=snapshot,
            hint_index=request.hint_index,
            total_hints=len(hints),
            hint=hints[request.hint_index - 1],
            event=result.event,
            mastery=result.mastery,
        )

    async def reveal_answer(
        self,
        course_id: str,
        exercise_key: str,
        request: CourseExerciseRevealRequest,
    ) -> CourseExerciseRevealResponse:
        _version, _chapter, version_id, exercise, snapshot = (
            await self._exercise_scope(
                course_id,
                request.chapter_key,
                exercise_key,
                request.snapshot_token,
            )
        )
        if request.concept_key not in exercise.blueprint.concept_keys:
            raise InvalidInputError(
                "Exercise does not cover the requested concept stable key."
            )
        transfer = exercise.blueprint.transfer_task if exercise.is_core else None
        reveal_request = CourseLearningEventRequest(
            snapshot_token=snapshot,
            idempotency_key=request.idempotency_key,
            chapter_key=request.chapter_key,
            concept_key=request.concept_key,
            exercise_key=exercise_key,
            kind="answer_revealed",
            payload={
                "attempt_key": request.attempt_key,
                "transfer_task_key": transfer.key if transfer else None,
            },
        )
        revealed = await self._event_for_action(
            course_id, version_id, reveal_request
        )
        required: LearningEvent | None = None
        if transfer is not None:
            gate_key = "gate-" + hashlib.sha256(
                request.idempotency_key.encode("utf-8")
            ).hexdigest()
            gate_request = CourseLearningEventRequest(
                snapshot_token=snapshot,
                idempotency_key=gate_key,
                chapter_key=request.chapter_key,
                concept_key=request.concept_key,
                exercise_key=exercise_key,
                kind="transfer_required",
                payload={
                    "attempt_key": request.attempt_key,
                    "transfer_task_key": transfer.key,
                },
            )
            required = await self._event_for_action(
                course_id,
                version_id,
                gate_request,
                occurred_at=revealed.occurred_at,
            )
        mastery = await self.learning_service.append_reveal_events(
            revealed, required
        )
        events = (revealed,) if required is None else (revealed, required)
        return CourseExerciseRevealResponse(
            snapshot_token=snapshot,
            answer=self.assessment_service.reveal_grader_answer(
                exercise.blueprint.grader
            ),
            transfer=self._transfer_response(transfer) if transfer else None,
            events=events,
            mastery=mastery,
        )

    async def grade_transfer(
        self,
        course_id: str,
        exercise_key: str,
        request: CourseTransferGradeRequest,
    ) -> CourseExerciseGradeResponse:
        _version, _chapter, version_id, exercise, snapshot = (
            await self._exercise_scope(
                course_id,
                request.chapter_key,
                exercise_key,
                request.snapshot_token,
            )
        )
        transfer = exercise.blueprint.transfer_task
        if transfer is None or transfer.key != request.transfer_task_key:
            raise InvalidInputError(
                "Transfer task is outside the current exercise scope."
            )
        if request.concept_key not in transfer.invariant_concept_keys:
            raise InvalidInputError(
                "Transfer task does not cover the requested concept stable key."
            )
        grade = await asyncio.to_thread(
            self.assessment_service.grade_transfer,
            transfer,
            request.answer,
        )
        if grade.advisory or grade.correct is not True:
            return CourseExerciseGradeResponse(
                grade=grade,
                mastery=None,
                event_key=None,
                snapshot_token=snapshot,
            )
        response_parts = (
            json.dumps(
                request.answer,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        event_key = "transfer-" + hashlib.sha256(
            "\x1f".join(
                (
                    course_id,
                    version_id,
                    request.chapter_key,
                    exercise_key,
                    request.concept_key,
                    request.source_attempt_key,
                    request.attempt_key,
                    request.transfer_task_key,
                )
            ).encode("utf-8")
        ).hexdigest()
        event = LearningEvent(
            event_id=event_key,
            course_id=course_id,
            course_version_id=version_id,
            chapter_key=request.chapter_key,
            concept_key=request.concept_key,
            exercise_key=exercise_key,
            kind="transfer_completed",
            payload=TransferCompletedPayload(
                attempt_key=request.attempt_key,
                source_attempt_key=request.source_attempt_key,
                transfer_task_key=request.transfer_task_key,
                response_parts=response_parts,
            ),
            occurred_at=self.clock(),
        )
        mastery = await self.learning_service.append_event(event)
        return CourseExerciseGradeResponse(
            grade=grade,
            mastery=mastery,
            event_key=event_key,
            snapshot_token=snapshot,
        )

    async def grade_exercise(
        self,
        course_id: str,
        exercise_key: str,
        request: CourseExerciseGradeRequest,
    ) -> CourseExerciseGradeResponse:
        _version, _chapter, version_id, exercise, snapshot = (
            await self._exercise_scope(
                course_id,
                request.chapter_key,
                exercise_key,
                request.snapshot_token,
            )
        )
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
                snapshot_token=snapshot,
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
            snapshot_token=snapshot,
        )


course_v2_service = CourseV2Service()


__all__ = ["CourseV2Service", "course_v2_service"]
