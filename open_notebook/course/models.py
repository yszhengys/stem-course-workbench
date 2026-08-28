"""Record-based Course domain models extended by migration 25."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, TypeVar

from pydantic import ConfigDict, Field, field_validator, model_validator
from surrealdb import RecordID

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.base import ObjectModel
from open_notebook.exceptions import NotFoundError, OpenNotebookError

from . import state_machine as sm
from .contracts import ModelSelection, SourceLocator

T = TypeVar("T", bound="CourseRecord")

DEFAULT_MODEL_POLICY: dict[str, ModelSelection] = {
    "outline": ModelSelection(adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"),
    "chapter_content": ModelSelection(adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"),
    "practice_labs": ModelSelection(adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"),
    "exercise_bank": ModelSelection(adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"),
    "exercise_bank_review": ModelSelection(adapter="codex_cli", model="gpt-5.6-luna", reasoning_effort="max"),
    "review": ModelSelection(adapter="codex_cli", model="gpt-5.6-luna", reasoning_effort="max"),
    "escalation": ModelSelection(adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"),
}


def _string_id(value: Any) -> Any:
    return str(value) if isinstance(value, RecordID) else value


def _record_fields(data: dict[str, Any], *fields: str) -> dict[str, Any]:
    for field in fields:
        if data.get(field) is not None:
            data[field] = ensure_record_id(str(data[field]))
    return data


def _record_arrays(data: dict[str, Any], *fields: str) -> dict[str, Any]:
    for field in fields:
        if field in data:
            data[field] = [ensure_record_id(str(value)) for value in data[field]]
    return data


def _rows(model: type[T], result: Any) -> list[T]:
    rows = result if isinstance(result, list) else [result] if result else []
    return [model(**row) for row in rows if isinstance(row, dict)]


class CourseRecord(ObjectModel):
    """Course records never use ObjectModel's cross-table polymorphic lookup."""

    model_config = ConfigDict(validate_assignment=True, arbitrary_types_allowed=True)

    @classmethod
    async def get(cls: type[T], id: str) -> T:
        if not id or id.partition(":")[0] != cls.table_name:
            raise NotFoundError(f"{cls.table_name} record not found")
        try:
            result = await repo_query(
                "SELECT * FROM $id", {"id": ensure_record_id(id)}
            )
        except Exception as exc:
            raise OpenNotebookError("Course record lookup failed") from exc
        rows = result if isinstance(result, list) else [result] if result else []
        if not rows or not isinstance(rows[0], dict):
            raise NotFoundError(f"{cls.table_name} record not found")
        try:
            return cls(**rows[0])
        except Exception as exc:
            raise OpenNotebookError("Course record is invalid") from exc

    @field_validator("id", mode="before")
    @classmethod
    def id_as_string(cls, value: Any) -> Any:
        return _string_id(value)


class Course(CourseRecord):
    table_name: ClassVar[str] = "course"
    nullable_fields: ClassVar[set[str]] = {
        "subject", "description", "outline", "config", "outline_version_id", "error_message"
    }

    title: str = Field(min_length=1, max_length=300)
    notebook: str = Field(min_length=1)
    subject: str | None = None
    description: str | None = None
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    status: str = sm.CourseStatus.DRAFT
    source_ids: list[str] = Field(default_factory=list, max_length=50)
    primary_source_ids: list[str] = Field(default_factory=list, max_length=50)
    supplement_source_ids: list[str] = Field(default_factory=list, max_length=50)
    outline_version_id: str | None = None
    error_message: str | None = None
    outline: dict[str, Any] | None = None
    config: dict[str, Any] | None = None

    @field_validator("notebook", "outline_version_id", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    @field_validator("source_ids", "primary_source_ids", "supplement_source_ids", mode="before")
    @classmethod
    def records_as_strings(cls, values: Any) -> Any:
        return [_string_id(value) for value in (values or [])]

    @model_validator(mode="after")
    def source_roles_do_not_overlap(self) -> "Course":
        primary = set(self.primary_source_ids)
        supplement = set(self.supplement_source_ids)
        if primary & supplement:
            raise ValueError("a source may have exactly one Course role")
        if set(self.source_ids) != primary | supplement:
            raise ValueError("source_ids must equal the union of role source IDs")
        return self

    def _prepare_save_data(self) -> dict[str, Any]:
        data = _record_fields(super()._prepare_save_data(), "notebook", "outline_version_id")
        return _record_arrays(data, "source_ids", "primary_source_ids", "supplement_source_ids")

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("course", self.status, target)
        await self.save()

    def model_for(self, stage: str) -> ModelSelection:
        raw = (self.config or {}).get("model_policy", {}).get(stage)
        return ModelSelection.model_validate(raw or DEFAULT_MODEL_POLICY[stage])

    @classmethod
    async def versions(cls, course_id: str) -> list["CourseVersion"]:
        result = await repo_query(
            "SELECT * FROM course_version WHERE course = $course_id ORDER BY version_no DESC",
            {"course_id": ensure_record_id(course_id)},
        )
        return _rows(CourseVersion, result)


class CourseVersion(CourseRecord):
    table_name: ClassVar[str] = "course_version"
    nullable_fields: ClassVar[set[str]] = {
        "outline_hash", "published_at", "outline_artifact", "input_hash", "approved_at", "confirmation",
        "upgrade_source_version", "upgrade_idempotency_key", "upgrade_confirmation",
    }

    course: str
    version_no: int = Field(ge=1)
    status: str = sm.VersionStatus.DRAFT
    outline_hash: str | None = None
    published_at: datetime | None = None
    outline_artifact: dict[str, Any] | None = None
    input_hash: str | None = None
    approved_at: datetime | None = None
    confirmation: str | None = None
    upgrade_source_version: str | None = None
    upgrade_idempotency_key: str | None = None
    upgrade_confirmation: str | None = None

    @field_validator("course", "upgrade_source_version", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        return _record_fields(
            super()._prepare_save_data(), "course", "upgrade_source_version"
        )

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("version", self.status, target)
        if target == sm.VersionStatus.PUBLISHED and self.published_at is None:
            self.published_at = datetime.now(timezone.utc)
        await self.save()

    @classmethod
    async def chapters(cls, version_id: str) -> list["Chapter"]:
        result = await repo_query(
            "SELECT * FROM chapter WHERE course_version = $version_id ORDER BY chapter_no",
            {"version_id": ensure_record_id(version_id)},
        )
        return _rows(Chapter, result)

    @classmethod
    async def labs(cls, version_id: str) -> list["Lab"]:
        result = await repo_query(
            "SELECT * FROM lab WHERE course_version = $version_id",
            {"version_id": ensure_record_id(version_id)},
        )
        return _rows(Lab, result)


class Chapter(CourseRecord):
    table_name: ClassVar[str] = "chapter"
    nullable_fields: ClassVar[set[str]] = {
        "content", "citations", "artifact", "input_hash", "published_at"
    }

    course_version: str
    chapter_no: int = Field(ge=1)
    title: str = Field(min_length=1)
    chapter_key: str = Field(min_length=1, max_length=100)
    version_no: int = Field(default=1, ge=1)
    artifact: dict[str, Any] | None = None
    input_hash: str | None = None
    status: str = sm.ChapterStatus.DRAFT
    published_at: datetime | None = None
    content: str | None = None
    review_status: str = sm.ChapterReviewStatus.PENDING
    validation_status: str = sm.ChapterValidationStatus.PENDING
    citations: list[dict[str, Any]] | None = None

    @field_validator("course_version", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        return _record_fields(super()._prepare_save_data(), "course_version")

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("chapter", self.status, target)
        if target == sm.ChapterStatus.PUBLISHED and self.published_at is None:
            self.published_at = datetime.now(timezone.utc)
        await self.save()

    async def transition_review(self, target: str) -> None:
        self.review_status = sm.transition("chapter_review", self.review_status, target)
        await self.save()

    async def transition_validation(self, target: str) -> None:
        self.validation_status = sm.transition("chapter_validation", self.validation_status, target)
        await self.save()


class Evidence(CourseRecord):
    table_name: ClassVar[str] = "evidence"
    nullable_fields: ClassVar[set[str]] = {
        "source", "title", "file_hash", "anchors", "source_role", "source_hash", "preview_path"
    }

    course: str
    source: str | None = None
    title: str | None = None
    kind: str = "pdf"
    file_hash: str | None = None
    anchors: dict[str, Any] | None = None
    status: str = sm.EvidenceStatus.PENDING
    source_role: str | None = None
    source_hash: str | None = None
    preview_path: str | None = None

    @field_validator("course", "source", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        return _record_fields(super()._prepare_save_data(), "course", "source")

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("evidence", self.status, target)
        await self.save()

    @classmethod
    async def list_by_course(cls, course_id: str) -> list["Evidence"]:
        result = await repo_query(
            "SELECT * FROM evidence WHERE course = $course_id ORDER BY created",
            {"course_id": ensure_record_id(course_id)},
        )
        return _rows(Evidence, result)


class Lab(CourseRecord):
    table_name: ClassVar[str] = "lab"
    nullable_fields: ClassVar[set[str]] = {"chapter", "prompt", "answer"}

    course_version: str
    chapter: str | None = None
    lab_type: str
    prompt: str | None = None
    payload: dict[str, Any]
    answer: dict[str, Any] | None = None

    @field_validator("course_version", "chapter", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        return _record_fields(super()._prepare_save_data(), "course_version", "chapter")

    @classmethod
    async def attempts(cls, lab_id: str) -> list["Attempt"]:
        result = await repo_query(
            "SELECT * FROM attempt WHERE lab = $lab_id ORDER BY created DESC",
            {"lab_id": ensure_record_id(lab_id)},
        )
        return _rows(Attempt, result)


class Attempt(CourseRecord):
    table_name: ClassVar[str] = "attempt"
    nullable_fields: ClassVar[set[str]] = {
        "result", "course", "course_version", "chapter", "chapter_key", "exercise_key",
        "answer", "hints_used", "answer_revealed", "transfer_completed", "orphan_status"
    }

    lab: str
    answers: dict[str, Any]
    status: str = sm.AttemptStatus.SUBMITTED
    result: dict[str, Any] | None = None
    course: str | None = None
    course_version: str | None = None
    chapter: str | None = None
    chapter_key: str | None = None
    exercise_key: str | None = None
    answer: str | None = None
    hints_used: int | None = Field(default=None, ge=0, le=5)
    answer_revealed: bool | None = None
    transfer_completed: bool | None = None
    orphan_status: str | None = None

    @field_validator("lab", "course", "course_version", "chapter", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        return _record_fields(super()._prepare_save_data(), "lab", "course", "course_version", "chapter")

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("attempt", self.status, target)
        await self.save()


class Progress(CourseRecord):
    table_name: ClassVar[str] = "progress"
    nullable_fields: ClassVar[set[str]] = {"chapter", "chapter_key", "block_key"}

    course: str
    chapter: str | None = None
    chapter_key: str | None = None
    block_key: str | None = None
    orphan_status: str = "active"
    status: str = sm.ProgressStatus.NOT_STARTED

    @field_validator("course", "chapter", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        return _record_fields(super()._prepare_save_data(), "course", "chapter")

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("progress", self.status, target)
        await self.save()

    @classmethod
    async def list_by_course(cls, course_id: str) -> list["Progress"]:
        result = await repo_query(
            "SELECT * FROM progress WHERE course = $course_id",
            {"course_id": ensure_record_id(course_id)},
        )
        return _rows(Progress, result)


class CourseNote(CourseRecord):
    table_name: ClassVar[str] = "course_note"
    nullable_fields: ClassVar[set[str]] = {"chapter", "chapter_key", "block_key"}

    course: str
    chapter: str | None = None
    chapter_key: str | None = None
    block_key: str | None = None
    orphan_status: str = "active"
    content: str

    @field_validator("course", "chapter", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        return _record_fields(super()._prepare_save_data(), "course", "chapter")

    @classmethod
    async def list_by_course(cls, course_id: str) -> list["CourseNote"]:
        result = await repo_query(
            "SELECT * FROM course_note WHERE course = $course_id ORDER BY created",
            {"course_id": ensure_record_id(course_id)},
        )
        return _rows(CourseNote, result)


class CourseEvidenceAnchor(CourseRecord):
    table_name: ClassVar[str] = "course_evidence_anchor"
    nullable_fields: ClassVar[set[str]] = {"evidence", "preview_path"}

    course: str
    source: str
    evidence: str | None = None
    anchor_id: str
    locator: SourceLocator
    quote_sha256: str
    source_role: str = "PRIMARY"
    preview_path: str | None = None
    is_current: bool = True

    @field_validator("course", "source", "evidence", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        return _record_fields(super()._prepare_save_data(), "course", "source", "evidence")


class CourseGenerationRun(CourseRecord):
    table_name: ClassVar[str] = "course_generation_run"
    nullable_fields: ClassVar[set[str]] = {
        "course_version", "chapter", "chapter_key", "reasoning_effort", "output_hash", "command", "error_message"
    }

    course: str
    course_version: str | None = None
    chapter: str | None = None
    chapter_key: str | None = None
    stage: str
    adapter: str
    model: str
    reasoning_effort: str | None = None
    status: str = sm.RunStatus.QUEUED
    prompt_version: str
    input_hash: str
    output_hash: str | None = None
    command: str | None = None
    error_message: str | None = None

    @field_validator("course", "course_version", "chapter", "command", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        return _record_fields(super()._prepare_save_data(), "course", "course_version", "chapter", "command")

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("run", self.status, target)
        await self.save()


class CourseValidationFinding(CourseRecord):
    table_name: ClassVar[str] = "course_validation_finding"
    nullable_fields: ClassVar[set[str]] = {
        "course_version", "chapter", "generation_run", "chapter_key", "resolution_reason"
    }

    course: str
    course_version: str | None = None
    chapter: str | None = None
    generation_run: str | None = None
    chapter_key: str | None = None
    finding: dict[str, Any]
    severity: str
    status: str = "open"
    resolution_reason: str | None = None

    @field_validator("course", "course_version", "chapter", "generation_run", mode="before")
    @classmethod
    def record_as_string(cls, value: Any) -> Any:
        return _string_id(value)

    def _prepare_save_data(self) -> dict[str, Any]:
        return _record_fields(
            super()._prepare_save_data(), "course", "course_version", "chapter", "generation_run"
        )
