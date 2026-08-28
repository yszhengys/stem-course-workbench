"""Strongly typed record models introduced by Course migration 26."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from open_notebook.exceptions import InvalidInputError

from .contracts import ModelSelection
from .models import CourseRecord, _record_arrays, _record_fields, _string_id
from .v2_contracts import (
    CourseBundleManifest,
    DifficultyVector,
    DraftOperation,
    ExerciseBlueprint,
    ExerciseVerification,
    GraderSpec,
    LearningEvent,
    LearningEventKind,
    LearningEventPayload,
    MasteryStatus,
    PendingTransfer,
    Sha256,
    StableKey,
    TutorResponse,
    TutorTurn,
    ValidationCheck,
)


class CourseImmutableRecordError(InvalidInputError):
    """Raised when append-only audit data would be updated or deleted."""


class AppendOnlyCourseRecord(CourseRecord):
    async def save(self) -> None:
        if self.id is not None:
            raise CourseImmutableRecordError(
                f"{self.table_name} records are append-only"
            )
        await super().save()

    async def delete(self) -> bool:
        raise CourseImmutableRecordError(f"{self.table_name} records are append-only")


class CourseExercise(CourseRecord):
    table_name: ClassVar[str] = "course_exercise"
    nullable_fields: ClassVar[set[str]] = {"generation_run"}

    course: str
    course_version: str
    chapter: str
    chapter_key: StableKey
    exercise_key: StableKey
    blueprint: ExerciseBlueprint
    source_anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    difficulty: DifficultyVector
    grader: GraderSpec
    is_core: bool = False
    is_gating: bool = False
    is_source_level: bool = False
    verification: ExerciseVerification = Field(
        default_factory=lambda: ExerciseVerification(
            level="L1", method="self_consistency"
        )
    )
    generation_run: str | None = None
    review_run_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)

    @field_validator(
        "course", "course_version", "chapter", "generation_run", mode="before"
    )
    @classmethod
    def records_as_strings(cls, value: Any) -> Any:
        return _string_id(value)

    @field_validator("review_run_ids", mode="before")
    @classmethod
    def record_array_as_strings(cls, values: Any) -> Any:
        return tuple(_string_id(value) for value in (values or ()))

    @model_validator(mode="after")
    def denormalized_fields_match_blueprint(self) -> "CourseExercise":
        if self.chapter_key != self.blueprint.chapter_key:
            raise ValueError("chapter_key must match the exercise blueprint")
        if self.exercise_key != self.blueprint.key:
            raise ValueError("exercise_key must match the exercise blueprint")
        if self.source_anchor_ids != self.blueprint.source_anchor_ids:
            raise ValueError("source anchors must match the exercise blueprint")
        if self.difficulty != self.blueprint.difficulty:
            raise ValueError("difficulty must match the exercise blueprint")
        if self.grader != self.blueprint.grader:
            raise ValueError("grader must match the exercise blueprint")
        if (
            self.is_core,
            self.is_gating,
            self.is_source_level,
        ) != (
            self.blueprint.is_core,
            self.blueprint.is_gating,
            self.blueprint.is_source_level,
        ):
            raise ValueError("exercise flags must match the exercise blueprint")
        return self

    def _prepare_save_data(self) -> dict[str, Any]:
        data = _record_fields(
            super()._prepare_save_data(),
            "course",
            "course_version",
            "chapter",
            "generation_run",
        )
        data = _record_arrays(data, "review_run_ids")
        data["blueprint"] = self.blueprint.model_dump(mode="json")
        data["difficulty"] = self.difficulty.model_dump(mode="json")
        data["grader"] = self.grader.model_dump(mode="json")
        data["source_anchor_ids"] = list(self.source_anchor_ids)
        data["verification"] = self.verification.model_dump(mode="json")
        return data


class CourseLearningEvent(AppendOnlyCourseRecord):
    table_name: ClassVar[str] = "course_learning_event"
    nullable_fields: ClassVar[set[str]] = {"concept_key", "exercise_key"}

    course: str
    course_version: str
    chapter: str
    chapter_key: StableKey
    concept_key: StableKey | None = None
    exercise_key: StableKey | None = None
    event_key: StableKey
    kind: LearningEventKind
    payload: LearningEventPayload
    occurred_at: datetime

    @field_validator("course", "course_version", "chapter", mode="before")
    @classmethod
    def records_as_strings(cls, value: Any) -> Any:
        return _string_id(value)

    @model_validator(mode="after")
    def event_contract_is_valid(self) -> "CourseLearningEvent":
        LearningEvent(
            event_id=self.event_key,
            course_id=self.course,
            course_version_id=self.course_version,
            chapter_key=self.chapter_key,
            concept_key=self.concept_key,
            exercise_key=self.exercise_key,
            kind=self.kind,
            payload=self.payload,
            occurred_at=self.occurred_at,
        )
        return self

    def _prepare_save_data(self) -> dict[str, Any]:
        data = _record_fields(
            super()._prepare_save_data(), "course", "course_version", "chapter"
        )
        data["payload"] = self.payload.model_dump(mode="json")
        return data


class CourseConceptMastery(CourseRecord):
    table_name: ClassVar[str] = "course_concept_mastery"
    nullable_fields: ClassVar[set[str]] = {"review_due_at", "last_event_at"}

    course: str
    course_version: str
    chapter_key: StableKey
    concept_key: StableKey
    status: MasteryStatus = "not_started"
    successful_exercise_keys: tuple[StableKey, ...] = Field(
        default_factory=tuple, max_length=200
    )
    unrevealed_success_count: int = Field(default=0, ge=0, le=200)
    pending_transfers: tuple[PendingTransfer, ...] = Field(
        default_factory=tuple, max_length=200
    )
    review_level: int = Field(default=0, ge=0, le=5)
    review_due_at: datetime | None = None
    last_event_at: datetime | None = None
    snapshot_hash: Sha256

    @field_validator("course", "course_version", mode="before")
    @classmethod
    def records_as_strings(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        data = _record_fields(super()._prepare_save_data(), "course", "course_version")
        data["successful_exercise_keys"] = list(self.successful_exercise_keys)
        data["pending_transfers"] = [
            item.model_dump(mode="json") for item in self.pending_transfers
        ]
        return data


class CourseTutorSession(CourseRecord):
    table_name: ClassVar[str] = "course_tutor_session"

    course: str
    course_version: str
    chapter: str
    chapter_key: StableKey
    model_selection: ModelSelection
    status: Literal["active", "closed", "stale"] = "active"

    @field_validator("course", "course_version", "chapter", mode="before")
    @classmethod
    def records_as_strings(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        data = _record_fields(
            super()._prepare_save_data(), "course", "course_version", "chapter"
        )
        data["model_selection"] = self.model_selection.model_dump(mode="json")
        return data


class CourseTutorOperation(AppendOnlyCourseRecord):
    """Immutable reservation binding one message identity to one request."""

    table_name: ClassVar[str] = "course_tutor_operation"

    course: str
    course_version: str
    session: str
    chapter_key: StableKey
    operation_identity: StableKey
    operation_key: StableKey
    request_fingerprint: Sha256

    @field_validator("course", "course_version", "session", mode="before")
    @classmethod
    def records_as_strings(cls, value: Any) -> Any:
        return _string_id(value)

    @model_validator(mode="after")
    def operation_key_matches_fingerprint(self) -> "CourseTutorOperation":
        if self.operation_key != (
            f"{self.operation_identity}-{self.request_fingerprint[:32]}"
        ):
            raise ValueError("Tutor operation key must match its request fingerprint")
        return self

    def _prepare_save_data(self) -> dict[str, Any]:
        return _record_fields(
            super()._prepare_save_data(), "course", "course_version", "session"
        )


class CourseTutorTurn(AppendOnlyCourseRecord):
    table_name: ClassVar[str] = "course_tutor_turn"
    nullable_fields: ClassVar[set[str]] = {"operation_key"}

    course: str
    course_version: str
    session: str
    chapter_key: StableKey
    operation_key: StableKey | None = None
    turn_no: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: str
    anchor_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    answer_revealed: bool = False
    insufficient_evidence: bool = False

    @field_validator("course", "course_version", "session", mode="before")
    @classmethod
    def records_as_strings(cls, value: Any) -> Any:
        return _string_id(value)

    @model_validator(mode="after")
    def turn_contract_is_valid(self) -> "CourseTutorTurn":
        turn = TutorTurn(
            turn_no=self.turn_no,
            role=self.role,
            content=self.content,
            anchor_ids=self.anchor_ids,
            answer_revealed=self.answer_revealed,
        )
        if self.role == "assistant":
            TutorResponse(
                session_id=self.session,
                turn=turn,
                insufficient_evidence=self.insufficient_evidence,
            )
        elif self.insufficient_evidence:
            raise ValueError("insufficient_evidence is only valid for assistant turns")
        return self

    def _prepare_save_data(self) -> dict[str, Any]:
        data = _record_fields(
            super()._prepare_save_data(), "course", "course_version", "session"
        )
        data["anchor_ids"] = list(self.anchor_ids)
        return data


class CourseDraftRevision(AppendOnlyCourseRecord):
    table_name: ClassVar[str] = "course_draft_revision"
    nullable_fields: ClassVar[set[str]] = {"parent_revision"}

    course: str
    course_version: str
    chapter: str
    chapter_key: StableKey
    revision_no: int = Field(ge=1)
    parent_revision: str | None = None
    base_artifact_hash: Sha256
    artifact_hash: Sha256
    operation: DraftOperation
    invalidated_checks: tuple[ValidationCheck, ...] = Field(
        default_factory=tuple, max_length=6
    )
    status: Literal["draft", "validated"] = "draft"

    @field_validator(
        "course", "course_version", "chapter", "parent_revision", mode="before"
    )
    @classmethod
    def records_as_strings(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        data = _record_fields(
            super()._prepare_save_data(),
            "course",
            "course_version",
            "chapter",
            "parent_revision",
        )
        data["operation"] = self.operation.model_dump(mode="json")
        data["invalidated_checks"] = list(self.invalidated_checks)
        return data


class CourseExport(CourseRecord):
    table_name: ClassVar[str] = "course_export"
    nullable_fields: ClassVar[set[str]] = {
        "bundle_path",
        "manifest",
        "error_message",
    }

    course: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"] = "queued"
    bundle_path: str | None = None
    manifest: CourseBundleManifest | None = None
    error_message: str | None = None

    @field_validator("course", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        data = _record_fields(super()._prepare_save_data(), "course")
        data["manifest"] = (
            self.manifest.model_dump(mode="json") if self.manifest else None
        )
        return data


__all__ = [
    "AppendOnlyCourseRecord",
    "CourseConceptMastery",
    "CourseDraftRevision",
    "CourseExercise",
    "CourseExport",
    "CourseImmutableRecordError",
    "CourseLearningEvent",
    "CourseTutorSession",
    "CourseTutorOperation",
    "CourseTutorTurn",
]
