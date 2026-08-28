"""Structured, optimistic-concurrency authoring boundary for Course V2."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, cast
from uuid import uuid4

from pydantic import Field, model_validator

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import InvalidInputError

from .assessment_service import AssessmentService
from .contracts import ChapterArtifact, CourseOutlineArtifact, ValidationFinding
from .generation_service import CourseGenerationService
from .models import Chapter, Course, CourseGenerationRun, CourseVersion
from .task_backend import CourseTaskBackend
from .v2_contracts import (
    DraftOperation,
    DraftRevision,
    ExerciseBlueprint,
    ReplaceExerciseOperation,
    ReplaceFormulaOperation,
    ReplaceLabOperation,
    ReplaceTextOperation,
    ReplaceTransferOperation,
    Sha256,
    V2Contract,
    ValidationCheck,
)
from .v2_models import CourseDraftRevision, CourseExercise
from .workflow_service import _artifact_hash, artifact_replay_hash

RevisionStatus = Literal["draft", "validated"]
_EDITABLE_CHAPTER_STATES = frozenset({"reviewing", "blocked"})
_EDITABLE_VERSION_STATES = frozenset({"draft", "generating"})
_INVALIDATED_CHECKS: dict[str, tuple[ValidationCheck, ...]] = {
    "replace_text": ("citation", "structure"),
    "replace_formula": ("formula", "unit", "numeric"),
    "replace_exercise": ("unit", "numeric", "physics", "citation", "structure"),
    "replace_transfer": ("unit", "numeric", "physics", "citation", "structure"),
    "replace_lab": ("physics", "citation", "structure"),
}
_VALIDATION_CHECK_ORDER: tuple[ValidationCheck, ...] = (
    "formula",
    "unit",
    "numeric",
    "physics",
    "citation",
    "structure",
)


class DraftConflictError(InvalidInputError):
    """Raised when a draft identity, revision, or operation is stale."""


class DraftImmutableError(InvalidInputError):
    """Raised when an approved or published artifact is edited."""


class LearningUpgradeConflictError(InvalidInputError):
    """Raised when a learning upgrade is stale or another upgrade is active."""


@dataclass(frozen=True, slots=True)
class LearningUpgradeResult:
    """One immutable Course-version upgrade prepared for exercise regeneration."""

    source_version_id: str
    version: CourseVersion
    chapters: tuple[Chapter, ...]


class DraftScope(V2Contract):
    """Server-resolved ownership for one current authoring chapter."""

    course_id: str = Field(pattern=r"^course:[^:]+$")
    course_version_id: str = Field(pattern=r"^course_version:[^:]+$")
    chapter_id: str = Field(pattern=r"^chapter:[^:]+$")
    chapter_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    chapter_status: str = Field(min_length=1, max_length=50)
    version_status: str = Field(min_length=1, max_length=50)
    allowed_anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=500)

    @model_validator(mode="after")
    def anchors_are_unique(self) -> "DraftScope":
        if len(self.allowed_anchor_ids) != len(set(self.allowed_anchor_ids)):
            raise ValueError("Draft evidence anchors must be unique")
        return self


def _document_hash(
    artifact: ChapterArtifact,
    exercises: tuple[ExerciseBlueprint, ...],
) -> str:
    payload = {
        "artifact": artifact.model_dump(mode="json"),
        "exercises": [item.model_dump(mode="json") for item in exercises],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


class DraftState(V2Contract):
    """Immutable snapshot returned to an editor before one atomic operation."""

    scope: DraftScope
    artifact: ChapterArtifact
    exercises: tuple[ExerciseBlueprint, ...] = Field(default_factory=tuple, max_length=500)
    revision_no: int = Field(default=0, ge=0)
    revision_id: str | None = Field(
        default=None, pattern=r"^course_draft_revision:[^:]+$"
    )
    revision_status: RevisionStatus | None = None
    invalidated_checks: tuple[ValidationCheck, ...] = Field(
        default_factory=tuple,
        max_length=6,
    )
    chapter_updated: datetime | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def revision_identity_is_complete(self) -> "DraftState":
        if self.artifact.chapter_key != self.scope.chapter_key:
            raise ValueError("Draft artifact chapter key does not match its scope")
        if any(item.chapter_key != self.scope.chapter_key for item in self.exercises):
            raise ValueError("Draft exercise escaped its chapter scope")
        if self.revision_no == 0:
            if self.revision_id is not None or self.revision_status is not None:
                raise ValueError("An initial draft cannot have revision metadata")
        elif self.revision_id is None or self.revision_status is None:
            raise ValueError("A persisted draft revision requires complete metadata")
        return self

    @property
    def artifact_hash(self) -> Sha256:
        return cast(Sha256, _document_hash(self.artifact, self.exercises))

    @property
    def revision_token(self) -> Sha256:
        payload = (
            self.scope.course_id,
            self.scope.course_version_id,
            self.scope.chapter_id,
            self.revision_no,
            self.artifact_hash,
        )
        return cast(
            Sha256,
            hashlib.sha256(
                json.dumps(payload, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        )

    @property
    def editable(self) -> bool:
        return (
            self.scope.chapter_status in _EDITABLE_CHAPTER_STATES
            and self.scope.version_status in _EDITABLE_VERSION_STATES
        )


class DraftChange(V2Contract):
    draft: DraftState
    revision: DraftRevision


class DraftValidationResult(V2Contract):
    valid: bool
    checked: tuple[ValidationCheck, ...] = Field(max_length=6)
    findings: tuple[ValidationFinding, ...] = Field(default_factory=tuple, max_length=500)


def _human_provenance(anchor_ids: tuple[str, ...]) -> str:
    return "adapted" if anchor_ids else "pedagogical"


def _ensure_anchor_subset(scope: DraftScope, anchor_ids: tuple[str, ...]) -> None:
    if not set(anchor_ids).issubset(set(scope.allowed_anchor_ids)):
        raise DraftConflictError("Draft operation cites evidence outside the Course.")


@dataclass(slots=True)
class AuthoringService:
    """Own immutable revisions, targeted checks, and atomic draft commits."""

    task_backend: CourseTaskBackend | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    @staticmethod
    def _upgrade_id(table: str, *parts: str) -> str:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:48]
        return f"{table}:{digest}"

    @staticmethod
    async def _learning_upgrade_result(
        course_id: str,
        version: CourseVersion,
    ) -> LearningUpgradeResult:
        if (
            version.id is None
            or version.course != course_id
            or version.upgrade_source_version is None
            or version.upgrade_confirmation != "创建学习升级版本"
            or version.upgrade_idempotency_key is None
        ):
            raise LearningUpgradeConflictError(
                "Learning upgrade version lineage is incomplete."
            )
        chapters = tuple(
            sorted(
                await CourseVersion.chapters(str(version.id)),
                key=lambda chapter: (chapter.chapter_no, chapter.chapter_key),
            )
        )
        if not chapters or any(
            chapter.course_version != str(version.id) for chapter in chapters
        ):
            raise LearningUpgradeConflictError(
                "Learning upgrade chapters are incomplete."
            )
        return LearningUpgradeResult(
            source_version_id=version.upgrade_source_version,
            version=version,
            chapters=chapters,
        )

    async def prepare_learning_upgrade(
        self,
        *,
        course_id: str,
        confirmation: str,
        idempotency_key: str,
    ) -> LearningUpgradeResult:
        """Clone published authoring state without copying any learner state."""

        if confirmation != "创建学习升级版本":
            raise ValueError("Type exactly 创建学习升级版本 to continue.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", idempotency_key):
            raise ValueError("Learning upgrade idempotency key is invalid.")

        course = await Course.get(course_id)
        if course.id is None or str(course.id) != course_id:
            raise LearningUpgradeConflictError("Course identity changed.")
        if course.outline_version_id is None:
            raise LearningUpgradeConflictError(
                "Course has no published version to upgrade."
            )
        pointed = await CourseVersion.get(course.outline_version_id)
        if pointed.course != course_id or pointed.id is None:
            raise LearningUpgradeConflictError(
                "Course current version does not belong to the Course."
            )
        if (
            pointed.upgrade_idempotency_key == idempotency_key
            and pointed.upgrade_confirmation == confirmation
            and pointed.upgrade_source_version is not None
        ):
            return await self._learning_upgrade_result(course_id, pointed)
        if pointed.status != "published":
            raise LearningUpgradeConflictError(
                "A different learning upgrade or authoring version is already active."
            )

        source_version = pointed
        source_version_id = str(source_version.id)
        if (
            source_version.outline_artifact is None
            or source_version.outline_hash
            != _artifact_hash(source_version.outline_artifact)
            or source_version.approved_at is None
            or source_version.confirmation != "确认大纲"
        ):
            raise LearningUpgradeConflictError(
                "Published Course outline approval is invalid."
            )
        outline = CourseOutlineArtifact.model_validate(
            source_version.outline_artifact
        )
        source_chapters = [
            chapter
            for chapter in await CourseVersion.chapters(source_version_id)
            if chapter.course_version == source_version_id
            and chapter.status == "published"
        ]
        by_key: dict[str, Chapter] = {}
        for chapter in source_chapters:
            if chapter.chapter_key in by_key:
                raise LearningUpgradeConflictError(
                    "Published Course chapter keys are ambiguous."
                )
            if chapter.id is None or chapter.artifact is None:
                raise LearningUpgradeConflictError(
                    "Published Course chapter artifact is incomplete."
                )
            artifact = ChapterArtifact.model_validate(chapter.artifact)
            if artifact.chapter_key != chapter.chapter_key:
                raise LearningUpgradeConflictError(
                    "Published Course chapter artifact is invalid."
                )
            by_key[chapter.chapter_key] = chapter
        expected_keys = {proposal.key for proposal in outline.chapters}
        if set(by_key) != expected_keys:
            raise LearningUpgradeConflictError(
                "Published Course chapters do not match the approved outline."
            )

        new_version_id = self._upgrade_id(
            "course_version", course_id, source_version_id, idempotency_key
        )
        version_content = {
            "course": ensure_record_id(course_id),
            "version_no": source_version.version_no + 1,
            "status": "generating",
            "outline_hash": source_version.outline_hash,
            "published_at": None,
            "outline_artifact": source_version.outline_artifact,
            "input_hash": source_version.input_hash,
            "approved_at": source_version.approved_at,
            "confirmation": source_version.confirmation,
            "upgrade_source_version": ensure_record_id(source_version_id),
            "upgrade_idempotency_key": idempotency_key,
            "upgrade_confirmation": confirmation,
        }
        chapter_records: list[dict[str, object]] = []
        source_to_target_chapter: dict[str, str] = {}
        for proposal in sorted(outline.chapters, key=lambda item: item.key):
            source_chapter = by_key[proposal.key]
            source_chapter_id = str(source_chapter.id)
            chapter_id = self._upgrade_id(
                "chapter", new_version_id, source_chapter_id
            )
            run_id = self._upgrade_id(
                "course_generation_run", new_version_id, source_chapter_id
            )
            logical_input_hash = hashlib.sha256(
                (
                    f"learning-upgrade\0{source_version_id}\0"
                    f"{source_chapter_id}\0{idempotency_key}"
                ).encode("utf-8")
            ).hexdigest()
            run = CourseGenerationRun(
                id=run_id,
                course=course_id,
                course_version=new_version_id,
                chapter=chapter_id,
                chapter_key=source_chapter.chapter_key,
                stage="chapter_content",
                adapter="open_notebook",
                model="learning-upgrade-clone",
                status="succeeded",
                prompt_version="learning-upgrade-v1",
                input_hash=logical_input_hash,
                output_hash=_artifact_hash(
                    {"output": source_chapter.artifact or {}}
                ),
            )
            chapter_records.append(
                {
                    "id": ensure_record_id(chapter_id),
                    "content": {
                        "course_version": ensure_record_id(new_version_id),
                        "chapter_no": source_chapter.chapter_no,
                        "title": source_chapter.title,
                        "chapter_key": source_chapter.chapter_key,
                        "version_no": 1,
                        "artifact": source_chapter.artifact,
                        "input_hash": artifact_replay_hash(run),
                        "status": "ready",
                        "published_at": None,
                        "content": source_chapter.content,
                        "review_status": "passed",
                        "validation_status": "passed",
                        "citations": source_chapter.citations,
                    },
                    "run_id": ensure_record_id(run_id),
                    "run_content": {
                        "course": ensure_record_id(course_id),
                        "course_version": ensure_record_id(new_version_id),
                        "chapter": ensure_record_id(chapter_id),
                        "chapter_key": source_chapter.chapter_key,
                        "stage": "chapter_content",
                        "adapter": "open_notebook",
                        "model": "learning-upgrade-clone",
                        "reasoning_effort": None,
                        "status": "succeeded",
                        "prompt_version": "learning-upgrade-v1",
                        "input_hash": logical_input_hash,
                        "output_hash": run.output_hash,
                        "command": None,
                        "error_message": None,
                    },
                    "source_id": ensure_record_id(source_chapter_id),
                    "source_updated": source_chapter.updated,
                }
            )
            source_to_target_chapter[source_chapter_id] = chapter_id

        lab_records: list[dict[str, object]] = []
        for source_lab in await CourseVersion.labs(source_version_id):
            if source_lab.id is None:
                raise LearningUpgradeConflictError(
                    "Published Course Lab has no identity."
                )
            target_chapter = (
                source_to_target_chapter.get(source_lab.chapter)
                if source_lab.chapter is not None
                else None
            )
            if source_lab.chapter is not None and target_chapter is None:
                raise LearningUpgradeConflictError(
                    "Published Course Lab belongs to an unknown chapter."
                )
            lab_id = self._upgrade_id(
                "lab", new_version_id, str(source_lab.id)
            )
            lab_records.append(
                {
                    "id": ensure_record_id(lab_id),
                    "content": {
                        "course_version": ensure_record_id(new_version_id),
                        "chapter": (
                            ensure_record_id(target_chapter)
                            if target_chapter is not None
                            else None
                        ),
                        "lab_type": source_lab.lab_type,
                        "prompt": source_lab.prompt,
                        "payload": source_lab.payload,
                        "answer": source_lab.answer,
                    },
                }
            )

        try:
            await repo_query(
                """
                BEGIN TRANSACTION;
                LET $current_course = (
                    SELECT VALUE id FROM $course
                    WHERE outline_version_id = $source_version
                      AND status = 'ready'
                );
                IF array::len($current_course) != 1 {
                    THROW 'Learning upgrade Course changed'
                };
                LET $source = (
                    SELECT VALUE id FROM $source_version
                    WHERE course = $course AND status = 'published'
                      AND time::micros(updated) = time::micros($source_updated)
                );
                IF array::len($source) != 1 {
                    THROW 'Learning upgrade source version changed'
                };
                LET $active = (
                    SELECT VALUE id FROM course_version
                    WHERE course = $course AND status IN ['draft', 'generating']
                );
                IF array::len($active) != 0 {
                    THROW 'A learning upgrade is already active'
                };
                FOR $chapter IN $chapters {
                    LET $source_chapter = (
                        SELECT VALUE id FROM $chapter.source_id
                        WHERE course_version = $source_version
                          AND status = 'published'
                          AND time::micros(updated) = time::micros($chapter.source_updated)
                    );
                    IF array::len($source_chapter) != 1 {
                        THROW 'Learning upgrade source chapter changed'
                    };
                };
                CREATE ONLY $new_version_id CONTENT $version_content;
                FOR $chapter IN $chapters {
                    CREATE ONLY $chapter.id CONTENT $chapter.content;
                    CREATE ONLY $chapter.run_id CONTENT $chapter.run_content;
                };
                FOR $lab IN $labs {
                    CREATE ONLY $lab.id CONTENT $lab.content;
                };
                LET $promoted = (
                    UPDATE $course SET
                        outline_version_id = $new_version_id,
                        status = 'generating', error_message = NONE
                    WHERE outline_version_id = $source_version
                      AND status = 'ready'
                    RETURN VALUE id
                );
                IF array::len($promoted) != 1 {
                    THROW 'Learning upgrade Course promotion conflict'
                };
                COMMIT TRANSACTION;
                """,
                {
                    "course": ensure_record_id(course_id),
                    "source_version": ensure_record_id(source_version_id),
                    "source_updated": source_version.updated,
                    "new_version_id": ensure_record_id(new_version_id),
                    "version_content": version_content,
                    "chapters": chapter_records,
                    "labs": lab_records,
                },
            )
        except RuntimeError as exc:
            refreshed_course = await Course.get(course_id)
            if refreshed_course.outline_version_id is not None:
                candidate = await CourseVersion.get(
                    refreshed_course.outline_version_id
                )
                if (
                    candidate.upgrade_source_version == source_version_id
                    and candidate.upgrade_idempotency_key == idempotency_key
                    and candidate.upgrade_confirmation == confirmation
                ):
                    return await self._learning_upgrade_result(
                        course_id, candidate
                    )
            raise LearningUpgradeConflictError(
                "A different learning upgrade or authoring version is already active."
            ) from exc

        created = await CourseVersion.get(new_version_id)
        return await self._learning_upgrade_result(course_id, created)

    @staticmethod
    def _assert_editable(draft: DraftState) -> None:
        if not draft.editable:
            raise DraftImmutableError("Approved or published Course artifacts are immutable.")

    @staticmethod
    def _require_unique_target(matches: int, label: str) -> None:
        if matches == 0:
            raise DraftConflictError(f"Draft {label} block does not exist.")
        if matches > 1:
            raise DraftConflictError(f"Draft {label} block target is ambiguous.")

    @staticmethod
    def _text_target_keys(artifact: ChapterArtifact) -> tuple[str, ...]:
        keys = ["purpose", *(section.key for section in artifact.sections)]
        attributed_lists = (
            ("prerequisite", artifact.prerequisites),
            ("objective", artifact.objectives),
            ("definition", artifact.definitions),
            ("misconception", artifact.misconceptions),
            ("pitfall", artifact.pitfalls),
            ("quick-reference", artifact.quick_reference),
        )
        for prefix, values in attributed_lists:
            keys.extend(f"{prefix}-{index + 1}" for index in range(len(values)))
        for example in artifact.worked_examples:
            keys.extend(
                (
                    f"worked-example-{example.key}-prompt",
                    f"worked-example-{example.key}-answer",
                    *(
                        f"worked-example-{example.key}-step-{index + 1}"
                        for index in range(len(example.steps))
                    ),
                )
            )
        for exercise in artifact.exercises:
            keys.extend(
                (
                    f"legacy-exercise-{exercise.key}-prompt",
                    f"legacy-exercise-{exercise.key}-answer",
                    f"legacy-exercise-{exercise.key}-transfer",
                    *(
                        f"legacy-exercise-{exercise.key}-hint-{index + 1}"
                        for index in range(len(exercise.hints))
                    ),
                )
            )
        return tuple(keys)

    @staticmethod
    def _replace_text(
        artifact: ChapterArtifact,
        operation: ReplaceTextOperation,
    ) -> ChapterArtifact:
        AuthoringService._require_unique_target(
            AuthoringService._text_target_keys(artifact).count(operation.block_key),
            "text",
        )
        updated = artifact.model_copy(deep=True)
        provenance = _human_provenance(operation.anchor_ids)

        for index, section in enumerate(updated.sections):
            if section.key == operation.block_key:
                updated.sections[index] = section.model_copy(
                    update={
                        "markdown": operation.text,
                        "anchor_ids": list(operation.anchor_ids),
                        "provenance": provenance,
                    }
                )
                return ChapterArtifact.model_validate(updated.model_dump(mode="json"))

        if operation.block_key == "purpose":
            updated.purpose = operation.text
            updated.attributions.purpose = updated.attributions.purpose.model_copy(
                update={
                    "anchor_ids": list(operation.anchor_ids),
                    "provenance": provenance,
                }
            )
            return ChapterArtifact.model_validate(updated.model_dump(mode="json"))

        attributed_lists = (
            ("prerequisite", "prerequisites"),
            ("objective", "objectives"),
            ("definition", "definitions"),
            ("misconception", "misconceptions"),
            ("pitfall", "pitfalls"),
            ("quick-reference", "quick_reference"),
        )
        for prefix, field_name in attributed_lists:
            values = getattr(updated, field_name)
            attributions = getattr(updated.attributions, field_name)
            for index, _value in enumerate(values):
                if operation.block_key == f"{prefix}-{index + 1}":
                    values[index] = operation.text
                    attributions[index] = attributions[index].model_copy(
                        update={
                            "anchor_ids": list(operation.anchor_ids),
                            "provenance": provenance,
                        }
                    )
                    return ChapterArtifact.model_validate(
                        updated.model_dump(mode="json")
                    )

        for example_index, example in enumerate(updated.worked_examples):
            example_targets: list[tuple[str, str, int | None]] = [
                (f"worked-example-{example.key}-prompt", "prompt", None),
                (f"worked-example-{example.key}-answer", "answer", None),
                *(
                    (f"worked-example-{example.key}-step-{step + 1}", "steps", step)
                    for step in range(len(example.steps))
                ),
            ]
            for key, field_name, step_index in example_targets:
                if operation.block_key != key:
                    continue
                changes: dict[str, object] = {
                    "anchor_ids": list(operation.anchor_ids),
                    "provenance": provenance,
                }
                if step_index is None:
                    changes[field_name] = operation.text
                else:
                    steps = list(example.steps)
                    steps[step_index] = operation.text
                    changes["steps"] = steps
                updated.worked_examples[example_index] = example.model_copy(
                    update=changes
                )
                return ChapterArtifact.model_validate(updated.model_dump(mode="json"))

        for exercise_index, exercise in enumerate(updated.exercises):
            exercise_targets: list[tuple[str, str, int | None]] = [
                (f"legacy-exercise-{exercise.key}-prompt", "prompt", None),
                (f"legacy-exercise-{exercise.key}-answer", "answer", None),
                (f"legacy-exercise-{exercise.key}-transfer", "transfer_task", None),
                *(
                    (f"legacy-exercise-{exercise.key}-hint-{hint + 1}", "hints", hint)
                    for hint in range(len(exercise.hints))
                ),
            ]
            for key, field_name, hint_index in exercise_targets:
                if operation.block_key != key:
                    continue
                changes = {
                    "anchor_ids": list(operation.anchor_ids),
                    "provenance": provenance,
                }
                if hint_index is None:
                    changes[field_name] = operation.text
                else:
                    hints = list(exercise.hints)
                    hints[hint_index] = operation.text
                    changes["hints"] = hints
                updated.exercises[exercise_index] = exercise.model_copy(update=changes)
                return ChapterArtifact.model_validate(updated.model_dump(mode="json"))

        raise DraftConflictError("Draft text block does not exist.")

    @staticmethod
    def _replace_formula(
        artifact: ChapterArtifact,
        operation: ReplaceFormulaOperation,
    ) -> ChapterArtifact:
        AuthoringService._require_unique_target(
            sum(formula.key == operation.block_key for formula in artifact.formulas),
            "formula",
        )
        updated = artifact.model_copy(deep=True)
        for index, formula in enumerate(updated.formulas):
            if formula.key != operation.block_key:
                continue
            updated.formulas[index] = formula.model_copy(
                update={
                    "latex": operation.latex,
                    "anchor_ids": list(operation.anchor_ids),
                    "provenance": _human_provenance(operation.anchor_ids),
                }
            )
            return ChapterArtifact.model_validate(updated.model_dump(mode="json"))
        raise DraftConflictError("Draft formula block does not exist.")

    @staticmethod
    def _replace_exercise(
        exercises: tuple[ExerciseBlueprint, ...],
        operation: ReplaceExerciseOperation,
        chapter_key: str,
    ) -> tuple[ExerciseBlueprint, ...]:
        replacement = operation.exercise
        if (
            replacement.key != operation.block_key
            or replacement.chapter_key != chapter_key
        ):
            raise DraftConflictError("Exercise stable identity cannot be changed.")
        AuthoringService._require_unique_target(
            sum(exercise.key == operation.block_key for exercise in exercises),
            "exercise",
        )
        updated = list(exercises)
        for index, exercise in enumerate(updated):
            if exercise.key == operation.block_key:
                updated[index] = replacement
                return tuple(updated)
        raise DraftConflictError("Draft exercise block does not exist.")

    @staticmethod
    def _replace_transfer(
        exercises: tuple[ExerciseBlueprint, ...],
        operation: ReplaceTransferOperation,
    ) -> tuple[ExerciseBlueprint, ...]:
        matching = tuple(
            exercise
            for exercise in exercises
            if operation.block_key
            in {
                exercise.key,
                exercise.transfer_task.key if exercise.transfer_task is not None else "",
            }
        )
        AuthoringService._require_unique_target(len(matching), "transfer")
        updated = list(exercises)
        for index, exercise in enumerate(updated):
            current = exercise.transfer_task
            if operation.block_key not in {
                exercise.key,
                current.key if current is not None else "",
            }:
                continue
            if current is None or operation.transfer_task.key != current.key:
                raise DraftConflictError("Transfer stable identity cannot be changed.")
            updated[index] = ExerciseBlueprint.model_validate(
                exercise.model_copy(
                    update={"transfer_task": operation.transfer_task}
                ).model_dump(mode="json")
            )
            return tuple(updated)
        raise DraftConflictError("Draft transfer block does not exist.")

    @staticmethod
    def _replace_lab(
        artifact: ChapterArtifact,
        operation: ReplaceLabOperation,
    ) -> ChapterArtifact:
        replacement = operation.lab_spec.as_lab_spec()
        if replacement.key != operation.block_key:
            raise DraftConflictError("Lab stable identity cannot be changed.")
        AuthoringService._require_unique_target(
            sum(lab.key == operation.block_key for lab in artifact.labs),
            "Lab",
        )
        updated = artifact.model_copy(deep=True)
        for index, lab in enumerate(updated.labs):
            if lab.key == operation.block_key:
                updated.labs[index] = replacement
                return ChapterArtifact.model_validate(updated.model_dump(mode="json"))
        raise DraftConflictError("Draft Lab block does not exist.")

    def apply_operation(
        self,
        draft: DraftState,
        operation: DraftOperation,
        *,
        expected_revision: str,
    ) -> DraftChange:
        """Apply one operation to a deep copy and create its immutable revision."""

        self._assert_editable(draft)
        if expected_revision != draft.revision_token:
            raise DraftConflictError("Draft revision token is stale.")

        anchor_ids: tuple[str, ...] = ()
        if isinstance(operation, (ReplaceTextOperation, ReplaceFormulaOperation)):
            anchor_ids = operation.anchor_ids
        elif isinstance(operation, ReplaceExerciseOperation):
            anchor_ids = operation.exercise.source_anchor_ids
            if operation.exercise.transfer_task is not None:
                anchor_ids += operation.exercise.transfer_task.anchor_ids
        elif isinstance(operation, ReplaceTransferOperation):
            anchor_ids = operation.transfer_task.anchor_ids
        elif isinstance(operation, ReplaceLabOperation):
            anchor_ids = tuple(operation.lab_spec.as_lab_spec().anchor_ids)
        _ensure_anchor_subset(draft.scope, anchor_ids)

        artifact = draft.artifact
        exercises = draft.exercises
        if isinstance(operation, ReplaceTextOperation):
            artifact = self._replace_text(artifact, operation)
        elif isinstance(operation, ReplaceFormulaOperation):
            artifact = self._replace_formula(artifact, operation)
        elif isinstance(operation, ReplaceExerciseOperation):
            exercises = self._replace_exercise(
                exercises, operation, draft.scope.chapter_key
            )
        elif isinstance(operation, ReplaceTransferOperation):
            exercises = self._replace_transfer(exercises, operation)
        elif isinstance(operation, ReplaceLabOperation):
            artifact = self._replace_lab(artifact, operation)

        invalidated = set(_INVALIDATED_CHECKS[operation.kind])
        if draft.revision_status == "draft":
            invalidated.update(draft.invalidated_checks)
        invalidated_checks = tuple(
            check for check in _VALIDATION_CHECK_ORDER if check in invalidated
        )
        next_no = draft.revision_no + 1
        changed = DraftState(
            scope=draft.scope,
            artifact=artifact,
            exercises=exercises,
            revision_no=next_no,
            revision_id=f"course_draft_revision:pending-{next_no}",
            revision_status="draft",
            invalidated_checks=invalidated_checks,
            chapter_updated=draft.chapter_updated,
        )
        if changed.artifact_hash == draft.artifact_hash:
            raise DraftConflictError("Draft operation did not change the artifact.")
        occurred_at = self.clock()
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise DraftConflictError("Draft clock must include a timezone.")
        revision = DraftRevision(
            revision_no=next_no,
            parent_revision_no=draft.revision_no or None,
            base_artifact_hash=draft.artifact_hash,
            artifact_hash=changed.artifact_hash,
            operation=operation,
            invalidated_checks=invalidated_checks,
            created_at=occurred_at.astimezone(timezone.utc),
        )
        return DraftChange(draft=changed, revision=revision)

    def validate_draft(
        self,
        draft: DraftState,
        revision: DraftRevision,
    ) -> DraftValidationResult:
        """Run only the deterministic checks invalidated by one revision."""

        if (
            revision.revision_no != draft.revision_no
            or revision.artifact_hash != draft.artifact_hash
        ):
            raise DraftConflictError("Draft validation revision is stale.")
        checked = revision.invalidated_checks
        known_anchors = set(draft.scope.allowed_anchor_ids)
        findings = CourseGenerationService.validate_chapter(
            draft.artifact,
            known_anchors,
        )
        assessment_checks = {
            "unit", "numeric", "physics", "citation", "structure"
        }
        if assessment_checks.issubset(set(checked)):
            findings.extend(
                AssessmentService.validate_bank(
                    draft.exercises,
                    known_anchor_ids=known_anchors,
                    expected_chapter_keys={draft.scope.chapter_key},
                )
            )
        normalized = tuple(
            finding.model_copy(update={"kind": "structure"})
            if finding.kind in {"review", "lab"}
            else finding
            for finding in findings
        )
        selected = tuple(
            finding for finding in normalized if finding.kind in set(checked)
        )
        blocking = any(
            finding.severity in {"high", "error"}
            or finding.status in {"uncertain", "manual_check"}
            for finding in selected
        )
        return DraftValidationResult(
            valid=not blocking,
            checked=checked,
            findings=selected,
        )

    async def get_draft(self, scope: DraftScope) -> DraftState:
        """Load one exact chapter snapshot and its latest append-only revision."""

        version = await CourseVersion.get(scope.course_version_id)
        chapter = await Chapter.get(scope.chapter_id)
        if (
            version.course != scope.course_id
            or chapter.course_version != scope.course_version_id
            or chapter.chapter_key != scope.chapter_key
            or version.status != scope.version_status
            or chapter.status != scope.chapter_status
            or chapter.artifact is None
        ):
            raise DraftConflictError("Draft scope changed before it was loaded.")
        artifact = ChapterArtifact.model_validate(chapter.artifact)
        exercise_rows = await repo_query(
            """
            SELECT * FROM course_exercise
            WHERE course = $course AND course_version = $version
              AND chapter = $chapter AND chapter_key = $chapter_key
            ORDER BY exercise_key;
            """,
            {
                "course": ensure_record_id(scope.course_id),
                "version": ensure_record_id(scope.course_version_id),
                "chapter": ensure_record_id(scope.chapter_id),
                "chapter_key": scope.chapter_key,
            },
        )
        exercise_records = tuple(
            CourseExercise(**row)
            for row in exercise_rows
            if isinstance(row, dict)
        )
        revision_rows = await repo_query(
            """
            SELECT * FROM course_draft_revision
            WHERE course = $course AND course_version = $version
              AND chapter = $chapter AND chapter_key = $chapter_key
            ORDER BY revision_no DESC LIMIT 1;
            """,
            {
                "course": ensure_record_id(scope.course_id),
                "version": ensure_record_id(scope.course_version_id),
                "chapter": ensure_record_id(scope.chapter_id),
                "chapter_key": scope.chapter_key,
            },
        )
        latest = (
            CourseDraftRevision(**revision_rows[0])
            if revision_rows and isinstance(revision_rows[0], dict)
            else None
        )
        state = DraftState(
            scope=scope,
            artifact=artifact,
            exercises=tuple(record.blueprint for record in exercise_records),
            revision_no=latest.revision_no if latest is not None else 0,
            revision_id=str(latest.id) if latest is not None else None,
            revision_status=latest.status if latest is not None else None,
            invalidated_checks=(
                latest.invalidated_checks
                if latest is not None and latest.status == "draft"
                else ()
            ),
            chapter_updated=chapter.updated,
        )
        if latest is not None and latest.artifact_hash != state.artifact_hash:
            raise DraftConflictError("Draft artifact no longer matches its revision.")
        return state

    async def _commit_change(
        self,
        original: DraftState,
        change: DraftChange,
    ) -> None:
        revision_id = f"course_draft_revision:{uuid4().hex}"
        operation = change.revision.operation
        exercise: ExerciseBlueprint | None = None
        if isinstance(operation, ReplaceExerciseOperation):
            exercise = operation.exercise
        elif isinstance(operation, ReplaceTransferOperation):
            exercise = next(
                item
                for item in change.draft.exercises
                if item.transfer_task is not None
                and item.transfer_task.key == operation.transfer_task.key
            )
        lab_payload: dict[str, object] | None = None
        lab_key: str | None = None
        if isinstance(operation, ReplaceLabOperation):
            lab = operation.lab_spec.as_lab_spec()
            lab_payload = lab.model_dump(mode="json", by_alias=True)
            lab_key = lab.key

        statement = """
        BEGIN TRANSACTION;
        LET $mutable_version = (
            SELECT VALUE id FROM $version
            WHERE course = $course AND status IN ['draft', 'generating'] LIMIT 1
        );
        IF array::len($mutable_version) != 1 {
            THROW 'Draft Course version is immutable'
        };
        LET $latest_revision = (
            SELECT id, revision_no, artifact_hash FROM course_draft_revision
            WHERE chapter = $chapter ORDER BY revision_no DESC LIMIT 1
        );
        IF array::len($latest_revision) != $expected_revision_count {
            THROW 'Draft revision changed'
        };
        IF $expected_revision_count = 1 {
            IF $latest_revision[0].revision_no != $expected_revision_no
               OR $latest_revision[0].artifact_hash != $expected_artifact_hash {
                THROW 'Draft revision changed'
            };
        };
        LET $chapter_update = (
            UPDATE $chapter SET
                artifact = $artifact,
                review_status = 'pending',
                validation_status = 'pending',
                updated = time::now()
            WHERE course_version = $version
              AND chapter_key = $chapter_key
              AND status IN ['reviewing', 'blocked']
              AND time::micros(updated) = time::micros($chapter_updated)
            RETURN VALUE id
        );
        IF array::len($chapter_update) != 1 {
            THROW 'Draft chapter changed'
        };
        IF $exercise_key != NONE {
            LET $exercise_update = (
                UPDATE course_exercise SET
                    blueprint = $exercise_blueprint,
                    source_anchor_ids = $exercise_source_anchor_ids,
                    difficulty = $exercise_difficulty,
                    grader = $exercise_grader,
                    is_core = $exercise_is_core,
                    is_gating = $exercise_is_gating,
                    is_source_level = $exercise_is_source_level,
                    updated = time::now()
                WHERE course = $course AND course_version = $version
                  AND chapter = $chapter AND chapter_key = $chapter_key
                  AND exercise_key = $exercise_key
                RETURN VALUE id
            );
            IF array::len($exercise_update) != 1 {
                THROW 'Draft exercise changed'
            };
        };
        IF $lab_key != NONE {
            LET $lab_update = (
                UPDATE lab SET payload = $lab_payload,
                    lab_type = $lab_type, updated = time::now()
                WHERE course_version = $version AND chapter = $chapter
                  AND payload.key = $lab_key
                RETURN VALUE id
            );
            IF array::len($lab_update) != 1 {
                THROW 'Draft Lab changed'
            };
        };
        CREATE ONLY $revision_id CONTENT $revision_content;
        COMMIT TRANSACTION;
        """
        revision_content = {
            "course": ensure_record_id(original.scope.course_id),
            "course_version": ensure_record_id(original.scope.course_version_id),
            "chapter": ensure_record_id(original.scope.chapter_id),
            "chapter_key": original.scope.chapter_key,
            "revision_no": change.revision.revision_no,
            "parent_revision": (
                ensure_record_id(original.revision_id)
                if original.revision_id is not None
                else None
            ),
            "base_artifact_hash": change.revision.base_artifact_hash,
            "artifact_hash": change.revision.artifact_hash,
            "operation": operation.model_dump(mode="json"),
            "invalidated_checks": list(change.revision.invalidated_checks),
            "status": "draft",
            "created": change.revision.created_at,
        }
        try:
            await repo_query(
                statement,
                {
                    "course": ensure_record_id(original.scope.course_id),
                    "version": ensure_record_id(original.scope.course_version_id),
                    "chapter": ensure_record_id(original.scope.chapter_id),
                    "chapter_key": original.scope.chapter_key,
                    "chapter_updated": original.chapter_updated,
                    "expected_revision_count": 1 if original.revision_no else 0,
                    "expected_revision_no": original.revision_no,
                    "expected_artifact_hash": original.artifact_hash,
                    "artifact": change.draft.artifact.model_dump(mode="json"),
                    "artifact_hash": change.draft.artifact_hash,
                    "exercise_key": exercise.key if exercise is not None else None,
                    "exercise_blueprint": (
                        exercise.model_dump(mode="json") if exercise is not None else None
                    ),
                    "exercise_source_anchor_ids": (
                        list(exercise.source_anchor_ids) if exercise is not None else []
                    ),
                    "exercise_difficulty": (
                        exercise.difficulty.model_dump(mode="json")
                        if exercise is not None else None
                    ),
                    "exercise_grader": (
                        exercise.grader.model_dump(mode="json")
                        if exercise is not None else None
                    ),
                    "exercise_is_core": exercise.is_core if exercise is not None else False,
                    "exercise_is_gating": exercise.is_gating if exercise is not None else False,
                    "exercise_is_source_level": (
                        exercise.is_source_level if exercise is not None else False
                    ),
                    "lab_key": lab_key,
                    "lab_payload": lab_payload,
                    "lab_type": lab_payload.get("kind") if lab_payload is not None else None,
                    "revision_id": ensure_record_id(revision_id),
                    "revision_content": revision_content,
                },
            )
        except RuntimeError as exc:
            raise DraftConflictError(
                "Draft changed before the operation could be saved."
            ) from exc

    async def save_operation(
        self,
        draft: DraftState,
        operation: DraftOperation,
        *,
        expected_revision: str,
    ) -> DraftState:
        change = self.apply_operation(
            draft, operation, expected_revision=expected_revision
        )
        await self._commit_change(draft, change)
        return await self.get_draft(draft.scope)

    async def validate_current(
        self,
        draft: DraftState,
        *,
        expected_revision: str,
    ) -> DraftValidationResult:
        self._assert_editable(draft)
        if expected_revision != draft.revision_token or draft.revision_id is None:
            raise DraftConflictError("Draft revision token is stale.")
        revision_rows = await repo_query(
            "SELECT * FROM course_draft_revision WHERE id = $revision LIMIT 1;",
            {"revision": ensure_record_id(draft.revision_id)},
        )
        if len(revision_rows) != 1:
            raise DraftConflictError("Draft revision no longer exists.")
        record = CourseDraftRevision(**revision_rows[0])
        revision = DraftRevision(
            revision_no=record.revision_no,
            parent_revision_no=(record.revision_no - 1 or None),
            base_artifact_hash=record.base_artifact_hash,
            artifact_hash=record.artifact_hash,
            operation=record.operation,
            invalidated_checks=record.invalidated_checks,
            created_at=record.created or self.clock(),
        )
        result = self.validate_draft(draft, revision)
        if not result.valid:
            return result
        try:
            await repo_query(
                """
                BEGIN TRANSACTION;
                LET $mutable_version = (
                    SELECT VALUE id FROM $version
                    WHERE course = $course AND status IN ['draft', 'generating'] LIMIT 1
                );
                LET $current_chapter = (
                    SELECT VALUE id FROM $chapter
                    WHERE course_version = $version AND chapter_key = $chapter_key
                      AND status IN ['reviewing', 'blocked']
                      AND time::micros(updated) = time::micros($chapter_updated)
                    LIMIT 1
                );
                LET $validated = (
                    UPDATE $revision SET status = 'validated'
                    WHERE chapter = $chapter AND revision_no = $revision_no
                      AND artifact_hash = $artifact_hash AND status = 'draft'
                    RETURN VALUE id
                );
                LET $latest = (
                    SELECT id, revision_no FROM course_draft_revision
                    WHERE chapter = $chapter ORDER BY revision_no DESC LIMIT 1
                );
                IF array::len($mutable_version) != 1
                   OR array::len($current_chapter) != 1
                   OR array::len($validated) != 1 OR array::len($latest) != 1
                   OR $latest[0].id != $revision {
                    THROW 'Draft validation revision changed'
                };
                COMMIT TRANSACTION;
                """,
                {
                    "revision": ensure_record_id(draft.revision_id),
                    "course": ensure_record_id(draft.scope.course_id),
                    "version": ensure_record_id(draft.scope.course_version_id),
                    "chapter": ensure_record_id(draft.scope.chapter_id),
                    "chapter_key": draft.scope.chapter_key,
                    "chapter_updated": draft.chapter_updated,
                    "revision_no": draft.revision_no,
                    "artifact_hash": draft.artifact_hash,
                },
            )
        except RuntimeError as exc:
            raise DraftConflictError(
                "Draft changed before validation could be saved."
            ) from exc
        return result


__all__ = [
    "AuthoringService",
    "DraftChange",
    "DraftConflictError",
    "DraftImmutableError",
    "DraftScope",
    "DraftState",
    "DraftValidationResult",
]
