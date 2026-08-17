"""Course module domain models (PDR-003).

Plain ObjectModel subclasses mapping 1:1 onto migration 24's tables. State
changes go through the transition helpers, which delegate to
`course.state_machine` — no model field is ever written directly.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Type, TypeVar

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.base import ObjectModel

from . import state_machine as sm

T = TypeVar("T", bound="ObjectModel")


def _rows(cls: Type[T], result: Any) -> List[T]:
    if not result:
        return []
    rows = result if isinstance(result, list) else [result]
    return [cls(**row) for row in rows if isinstance(row, dict)]


def _with_record_ids(data: Dict[str, Any], fields: tuple[str, ...]) -> Dict[str, Any]:
    """Convert string record fields to RecordID before insert/update.

    SCHEMAFULL record fields reject the plain string form; the surrealdb
    client serializes RecordID objects correctly (same pattern as
    Transformation._prepare_save_data upstream).
    """
    for field in fields:
        if data.get(field) is not None:
            data[field] = ensure_record_id(data[field])
    return data


class Course(ObjectModel):
    table_name: ClassVar[str] = "course"
    nullable_fields: ClassVar[set[str]] = {"subject", "description", "outline", "config"}

    title: str
    subject: Optional[str] = None
    description: Optional[str] = None
    status: str = sm.CourseStatus.DRAFT
    outline: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("course", self.status, target)
        await self.save()

    @classmethod
    async def versions(cls, course_id: str) -> List["CourseVersion"]:
        result = await repo_query(
            "SELECT * FROM course_version WHERE course = $course_id "
            "ORDER BY version_no DESC",
            {"course_id": ensure_record_id(course_id)},
        )
        return _rows(CourseVersion, result)


class CourseVersion(ObjectModel):
    table_name: ClassVar[str] = "course_version"
    nullable_fields: ClassVar[set[str]] = {"outline_hash", "published_at"}

    course: str
    version_no: int
    status: str = sm.VersionStatus.DRAFT
    outline_hash: Optional[str] = None
    published_at: Optional[datetime] = None

    def _prepare_save_data(self) -> Dict[str, Any]:
        return _with_record_ids(super()._prepare_save_data(), ("course",))

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("version", self.status, target)
        if target == sm.VersionStatus.PUBLISHED and self.published_at is None:
            self.published_at = datetime.now()
        await self.save()

    @classmethod
    async def chapters(cls, version_id: str) -> List["Chapter"]:
        result = await repo_query(
            "SELECT * FROM chapter WHERE course_version = $version_id "
            "ORDER BY chapter_no",
            {"version_id": ensure_record_id(version_id)},
        )
        return _rows(Chapter, result)

    @classmethod
    async def labs(cls, version_id: str) -> List["Lab"]:
        result = await repo_query(
            "SELECT * FROM lab WHERE course_version = $version_id",
            {"version_id": ensure_record_id(version_id)},
        )
        return _rows(Lab, result)


class Chapter(ObjectModel):
    table_name: ClassVar[str] = "chapter"
    nullable_fields: ClassVar[set[str]] = {"content", "citations"}

    course_version: str
    chapter_no: int
    title: str
    content: Optional[str] = None
    review_status: str = sm.ChapterReviewStatus.PENDING
    validation_status: str = sm.ChapterValidationStatus.PENDING
    citations: Optional[List[Dict[str, Any]]] = None

    def _prepare_save_data(self) -> Dict[str, Any]:
        return _with_record_ids(super()._prepare_save_data(), ("course_version",))

    async def transition_review(self, target: str) -> None:
        self.review_status = sm.transition(
            "chapter_review", self.review_status, target
        )
        await self.save()

    async def transition_validation(self, target: str) -> None:
        self.validation_status = sm.transition(
            "chapter_validation", self.validation_status, target
        )
        await self.save()


class Evidence(ObjectModel):
    table_name: ClassVar[str] = "evidence"
    nullable_fields: ClassVar[set[str]] = {"source", "title", "file_hash", "anchors"}

    course: str
    source: Optional[str] = None
    title: Optional[str] = None
    kind: str = "pdf"
    file_hash: Optional[str] = None
    anchors: Optional[Dict[str, Any]] = None
    status: str = sm.EvidenceStatus.PENDING

    def _prepare_save_data(self) -> Dict[str, Any]:
        return _with_record_ids(super()._prepare_save_data(), ("course", "source"))

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("evidence", self.status, target)
        await self.save()

    @classmethod
    async def list_by_course(cls, course_id: str) -> List["Evidence"]:
        result = await repo_query(
            "SELECT * FROM evidence WHERE course = $course_id ORDER BY created",
            {"course_id": ensure_record_id(course_id)},
        )
        return _rows(Evidence, result)


class Lab(ObjectModel):
    table_name: ClassVar[str] = "lab"
    nullable_fields: ClassVar[set[str]] = {"chapter", "prompt", "answer"}

    course_version: str
    chapter: Optional[str] = None
    lab_type: str
    prompt: Optional[str] = None
    payload: Dict[str, Any]
    answer: Optional[Dict[str, Any]] = None

    def _prepare_save_data(self) -> Dict[str, Any]:
        return _with_record_ids(
            super()._prepare_save_data(), ("course_version", "chapter")
        )

    @classmethod
    async def attempts(cls, lab_id: str) -> List["Attempt"]:
        result = await repo_query(
            "SELECT * FROM attempt WHERE lab = $lab_id ORDER BY created DESC",
            {"lab_id": ensure_record_id(lab_id)},
        )
        return _rows(Attempt, result)


class Attempt(ObjectModel):
    table_name: ClassVar[str] = "attempt"
    nullable_fields: ClassVar[set[str]] = {"result"}

    lab: str
    answers: Dict[str, Any]
    status: str = sm.AttemptStatus.SUBMITTED
    result: Optional[Dict[str, Any]] = None

    def _prepare_save_data(self) -> Dict[str, Any]:
        return _with_record_ids(super()._prepare_save_data(), ("lab",))

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("attempt", self.status, target)
        await self.save()


class Progress(ObjectModel):
    table_name: ClassVar[str] = "progress"
    nullable_fields: ClassVar[set[str]] = {"chapter"}

    course: str
    chapter: Optional[str] = None
    status: str = sm.ProgressStatus.NOT_STARTED

    def _prepare_save_data(self) -> Dict[str, Any]:
        return _with_record_ids(super()._prepare_save_data(), ("course", "chapter"))

    async def transition_to(self, target: str) -> None:
        self.status = sm.transition("progress", self.status, target)
        await self.save()

    @classmethod
    async def list_by_course(cls, course_id: str) -> List["Progress"]:
        result = await repo_query(
            "SELECT * FROM progress WHERE course = $course_id",
            {"course_id": ensure_record_id(course_id)},
        )
        return _rows(Progress, result)


class CourseNote(ObjectModel):
    table_name: ClassVar[str] = "course_note"
    nullable_fields: ClassVar[set[str]] = {"chapter"}

    course: str
    chapter: Optional[str] = None
    content: str

    def _prepare_save_data(self) -> Dict[str, Any]:
        return _with_record_ids(super()._prepare_save_data(), ("course", "chapter"))

    @classmethod
    async def list_by_course(cls, course_id: str) -> List["CourseNote"]:
        result = await repo_query(
            "SELECT * FROM course_note WHERE course = $course_id ORDER BY created",
            {"course_id": ensure_record_id(course_id)},
        )
        return _rows(CourseNote, result)
