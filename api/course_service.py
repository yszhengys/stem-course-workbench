"""Course application services; routers contain no persistence decisions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, TypeVar

from open_notebook.course import state_machine as sm
from open_notebook.course.models import (
    Attempt,
    Chapter,
    Course,
    CourseNote,
    CourseVersion,
    Lab,
    Progress,
)
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.base import ObjectModel
from open_notebook.domain.notebook import Notebook, Source
from open_notebook.exceptions import InvalidInputError, NotFoundError, OpenNotebookError

ModelT = TypeVar("ModelT", bound=ObjectModel)


class CourseApprovalError(InvalidInputError):
    """The explicit human confirmation is missing or malformed."""


class CourseConflictError(OpenNotebookError):
    """The request conflicts with current Course state or version."""


class CourseImmutableError(CourseConflictError):
    """A published Course artifact cannot be changed in place."""


def _has_table(record_id: str | None, table: str) -> bool:
    if not record_id:
        return False
    return record_id.partition(":")[0] == table


async def _typed_get(model: type[ModelT], record_id: str, table: str) -> ModelT:
    if not _has_table(record_id, table):
        raise NotFoundError(f"{table} record not found")
    return await model.get(record_id)


def _artifact_hash(artifact: dict[str, Any]) -> str:
    encoded = json.dumps(
        artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CourseService:
    @staticmethod
    async def create_course(
        *,
        title: str,
        subject: str | None = None,
        description: str | None = None,
        language: str = "zh-CN",
        config: dict[str, Any] | None = None,
        notebook_id: str | None = None,
    ) -> Course:
        created_notebook: Notebook | None = None
        if notebook_id is not None:
            notebook = await _typed_get(Notebook, notebook_id, "notebook")
            if not notebook.id:
                raise NotFoundError("notebook record not found")
        else:
            created_notebook = Notebook(
                name=title, description=f"STEM Course: {title}"
            )
            await created_notebook.save()
            if not created_notebook.id:
                raise OpenNotebookError("Course notebook creation failed")
            notebook_id = created_notebook.id

        course = Course(
            title=title,
            subject=subject,
            description=description,
            language=language,
            config=config,
            notebook=notebook_id,
        )
        try:
            await course.save()
        except Exception:
            if created_notebook is not None and created_notebook.id:
                await created_notebook.delete()
            raise
        return course

    @staticmethod
    async def list_courses() -> list[Course]:
        return await Course.get_all(order_by="created desc")

    @staticmethod
    async def get_course(course_id: str) -> Course:
        return await _typed_get(Course, course_id, "course")

    @staticmethod
    async def update_course(course_id: str, values: dict[str, Any]) -> Course:
        course = await CourseService.get_course(course_id)
        for field in ("title", "subject", "description", "language", "config"):
            if field in values and values[field] is not None:
                setattr(course, field, values[field])
        await course.save()
        return course

    @staticmethod
    async def delete_course(course_id: str) -> None:
        course = await CourseService.get_course(course_id)
        await course.delete()

    @staticmethod
    async def associate_source(
        course_id: str,
        source_id: str,
        role: Literal["PRIMARY", "SUPPLEMENT"],
    ) -> Course:
        course = await CourseService.get_course(course_id)
        await _typed_get(Notebook, course.notebook, "notebook")
        source = await _typed_get(Source, source_id, "source")
        if source_id in course.source_ids:
            existing_role = (
                "PRIMARY" if source_id in course.primary_source_ids else "SUPPLEMENT"
            )
            raise CourseConflictError(
                f"Source is already associated as {existing_role}"
            )
        await source.add_to_notebook(course.notebook)
        course.source_ids.append(source_id)
        if role == "PRIMARY":
            course.primary_source_ids.append(source_id)
        else:
            course.supplement_source_ids.append(source_id)
        await course.save()
        return course

    @staticmethod
    async def create_version(
        course_id: str, values: dict[str, Any]
    ) -> CourseVersion:
        course = await CourseService.get_course(course_id)
        versions = await Course.versions(course_id)
        next_no = max((version.version_no for version in versions), default=0) + 1
        version = CourseVersion(
            course=course_id,
            version_no=next_no,
            outline_hash=values.get("outline_hash"),
            outline_artifact=values.get("outline_artifact") or course.outline,
            input_hash=values.get("input_hash"),
        )
        await version.save()
        return version

    @staticmethod
    async def list_versions(course_id: str) -> list[CourseVersion]:
        await CourseService.get_course(course_id)
        return await Course.versions(course_id)

    @staticmethod
    async def approve_outline(
        course_id: str, version_id: str, confirmation: str
    ) -> CourseVersion:
        normalized = sm.normalize_approval(confirmation)
        if normalized != "确认大纲":
            raise CourseApprovalError("Type exactly: 确认大纲")
        course = await CourseService.get_course(course_id)
        if course.status != sm.CourseStatus.OUTLINE_READY:
            raise CourseConflictError("Course is not awaiting outline approval")
        if course.outline_version_id != version_id:
            raise CourseConflictError("Outline version is stale")
        version = await _typed_get(CourseVersion, version_id, "course_version")
        if version.course != course_id:
            raise CourseConflictError("Outline version is stale")
        if version.outline_artifact is None:
            raise CourseConflictError("Outline version has no artifact")
        version.confirmation = normalized
        version.approved_at = datetime.now(timezone.utc)
        version.outline_hash = _artifact_hash(version.outline_artifact)
        course.status = sm.transition("course", course.status, sm.CourseStatus.OUTLINE_APPROVED)
        await version.save()
        await course.save()
        return version

    @staticmethod
    async def create_chapter(
        version_id: str, values: dict[str, Any]
    ) -> Chapter:
        version = await _typed_get(CourseVersion, version_id, "course_version")
        if version.status == sm.VersionStatus.PUBLISHED:
            raise CourseImmutableError("Published course versions are immutable")
        chapter_key = values.get("chapter_key") or f"chapter-{values['chapter_no']}"
        existing = await CourseVersion.chapters(version_id)
        next_no = max(
            (
                chapter.version_no
                for chapter in existing
                if chapter.chapter_key == chapter_key
            ),
            default=0,
        ) + 1
        chapter = Chapter(
            course_version=version_id,
            chapter_no=values["chapter_no"],
            title=values["title"],
            chapter_key=chapter_key,
            version_no=next_no,
            artifact=values.get("artifact"),
            input_hash=values.get("input_hash"),
        )
        await chapter.save()
        return chapter

    @staticmethod
    async def list_chapters(version_id: str) -> list[Chapter]:
        await _typed_get(CourseVersion, version_id, "course_version")
        return await CourseVersion.chapters(version_id)

    @staticmethod
    async def update_chapter(
        version_id: str, chapter_id: str, values: dict[str, Any]
    ) -> Chapter:
        version = await _typed_get(CourseVersion, version_id, "course_version")
        chapter = await _typed_get(Chapter, chapter_id, "chapter")
        if chapter.course_version != version_id:
            raise NotFoundError("Chapter not found in course version")
        if version.status == sm.VersionStatus.PUBLISHED or chapter.status == sm.ChapterStatus.PUBLISHED:
            raise CourseImmutableError("Published artifacts are immutable")

        next_status = chapter.status
        next_review = chapter.review_status
        next_validation = chapter.validation_status
        if values.get("status") is not None:
            next_status = sm.transition("chapter", chapter.status, values["status"])
        if values.get("review_status") is not None:
            next_review = sm.transition(
                "chapter_review", chapter.review_status, values["review_status"]
            )
        if values.get("validation_status") is not None:
            next_validation = sm.transition(
                "chapter_validation",
                chapter.validation_status,
                values["validation_status"],
            )

        for field in ("title", "content", "citations", "artifact", "input_hash"):
            if field in values and values[field] is not None:
                setattr(chapter, field, values[field])
        chapter.status = next_status
        chapter.review_status = next_review
        chapter.validation_status = next_validation
        await chapter.save()
        return chapter

    @staticmethod
    async def publish_chapter(
        course_id: str, version_id: str, chapter_id: str
    ) -> Chapter:
        course = await CourseService.get_course(course_id)
        version = await _typed_get(CourseVersion, version_id, "course_version")
        chapter = await _typed_get(Chapter, chapter_id, "chapter")
        if version.course != course_id or chapter.course_version != version_id:
            raise NotFoundError("Chapter not found in Course")
        if chapter.status == sm.ChapterStatus.PUBLISHED:
            return chapter
        if chapter.status != sm.ChapterStatus.READY:
            raise CourseConflictError("Chapter is not ready for publication")
        if (
            chapter.review_status != sm.ChapterReviewStatus.PASSED
            or chapter.validation_status != sm.ChapterValidationStatus.PASSED
        ):
            raise CourseConflictError("Chapter review and validation must pass")
        if (
            course.outline_version_id != version_id
            or version.approved_at is None
            or version.outline_artifact is None
            or version.outline_hash != _artifact_hash(version.outline_artifact)
        ):
            raise CourseConflictError("Approved outline hash does not match")
        chapter.status = sm.transition("chapter", chapter.status, sm.ChapterStatus.PUBLISHED)
        chapter.published_at = datetime.now(timezone.utc)
        await chapter.save()
        return chapter

    @staticmethod
    async def publish_version(version_id: str) -> CourseVersion:
        version = await _typed_get(CourseVersion, version_id, "course_version")
        if version.status == sm.VersionStatus.PUBLISHED:
            return version
        chapters = await CourseVersion.chapters(version_id)
        if not chapters or any(
            chapter.status != sm.ChapterStatus.PUBLISHED for chapter in chapters
        ):
            raise CourseConflictError("All chapters must be published first")
        version.status = sm.VersionStatus.PUBLISHED
        version.published_at = datetime.now(timezone.utc)
        await version.save()
        return version

    @staticmethod
    async def create_lab(version_id: str, values: dict[str, Any]) -> Lab:
        version = await _typed_get(CourseVersion, version_id, "course_version")
        if version.status == sm.VersionStatus.PUBLISHED:
            raise CourseImmutableError("Published course versions are immutable")
        chapter_id = values.get("chapter")
        if chapter_id is not None:
            chapter = await _typed_get(Chapter, chapter_id, "chapter")
            if chapter.course_version != version_id:
                raise NotFoundError("Chapter not found in course version")
            if chapter.status == sm.ChapterStatus.PUBLISHED:
                raise CourseImmutableError("Published chapters are immutable")
        lab = Lab(course_version=version_id, **values)
        await lab.save()
        return lab

    @staticmethod
    async def list_labs(version_id: str) -> list[Lab]:
        await _typed_get(CourseVersion, version_id, "course_version")
        return await CourseVersion.labs(version_id)

    @staticmethod
    async def create_attempt(lab_id: str, values: dict[str, Any]) -> Attempt:
        lab = await _typed_get(Lab, lab_id, "lab")
        version = await _typed_get(CourseVersion, lab.course_version, "course_version")
        chapter_id = lab.chapter
        if chapter_id is not None:
            chapter = await _typed_get(Chapter, chapter_id, "chapter")
            if chapter.course_version != version.id:
                raise NotFoundError("Lab chapter ownership mismatch")
        attempt = Attempt(
            lab=lab_id,
            answers=values["answers"],
            course=version.course,
            course_version=lab.course_version,
            chapter=lab.chapter,
            chapter_key=values.get("chapter_key"),
            exercise_key=values.get("exercise_key"),
        )
        await attempt.save()
        return attempt

    @staticmethod
    async def list_attempts(lab_id: str) -> list[Attempt]:
        await _typed_get(Lab, lab_id, "lab")
        return await Lab.attempts(lab_id)

    @staticmethod
    async def transition_attempt(attempt_id: str, target: str) -> Attempt:
        attempt = await _typed_get(Attempt, attempt_id, "attempt")
        lab = await _typed_get(Lab, attempt.lab, "lab")
        if attempt.course_version and attempt.course_version != lab.course_version:
            raise NotFoundError("Attempt ownership mismatch")
        if attempt.chapter and attempt.chapter != lab.chapter:
            raise NotFoundError("Attempt ownership mismatch")
        attempt.status = sm.transition("attempt", attempt.status, target)
        await attempt.save()
        return attempt

    @staticmethod
    async def list_progress(course_id: str) -> list[Progress]:
        await CourseService.get_course(course_id)
        return await Progress.list_by_course(course_id)

    @staticmethod
    async def upsert_progress(course_id: str, values: dict[str, Any]) -> Progress:
        await CourseService.get_course(course_id)
        chapter_id = values.get("chapter")
        if chapter_id:
            chapter = await _typed_get(Chapter, chapter_id, "chapter")
            version = await _typed_get(CourseVersion, chapter.course_version, "course_version")
            if version.course != course_id:
                raise NotFoundError("Chapter not found in Course")
        chapter_key = values.get("chapter_key")
        result = await repo_query(
            "SELECT * FROM progress WHERE course = $course AND chapter_key = $chapter_key",
            {"course": ensure_record_id(course_id), "chapter_key": chapter_key},
        )
        progress = Progress(**result[0]) if result else Progress(
            course=course_id,
            chapter=chapter_id,
            chapter_key=chapter_key,
            block_key=values.get("block_key"),
        )
        progress.status = sm.transition("progress", progress.status, values["status"])
        await progress.save()
        return progress

    @staticmethod
    async def list_notes(course_id: str) -> list[CourseNote]:
        await CourseService.get_course(course_id)
        return await CourseNote.list_by_course(course_id)

    @staticmethod
    async def create_note(course_id: str, values: dict[str, Any]) -> CourseNote:
        await CourseService.get_course(course_id)
        chapter_id = values.get("chapter")
        if chapter_id:
            chapter = await _typed_get(Chapter, chapter_id, "chapter")
            version = await _typed_get(CourseVersion, chapter.course_version, "course_version")
            if version.course != course_id:
                raise NotFoundError("Chapter not found in Course")
        note = CourseNote(course=course_id, **values)
        await note.save()
        return note

    @staticmethod
    async def delete_note(course_id: str, note_id: str) -> None:
        await CourseService.get_course(course_id)
        note = await _typed_get(CourseNote, note_id, "course_note")
        if note.course != course_id:
            raise NotFoundError("Course note not found")
        await note.delete()
