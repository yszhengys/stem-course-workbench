"""Course application services; routers contain no persistence decisions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, TypeVar

from open_notebook.ai.models import Model
from open_notebook.course import state_machine as sm
from open_notebook.course.models import (
    DEFAULT_MODEL_POLICY,
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
    try:
        return await model.get(record_id)
    except NotFoundError as exc:
        # ObjectModel.get historically collapses every repository/validation
        # failure into NotFoundError. Preserve a real missing-record result, but
        # promote wrapped operational failures so the router returns a sanitized
        # server error rather than a misleading 404.
        cause = exc.__cause__ or exc.__context__
        if cause is not None and not isinstance(
            cause, (NotFoundError, InvalidInputError)
        ):
            raise OpenNotebookError("Course record lookup failed") from exc
        raise NotFoundError(f"{table} record not found") from None


def _artifact_hash(artifact: dict[str, Any]) -> str:
    encoded = json.dumps(
        artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_keys(artifact: dict[str, Any] | None, field: str) -> set[str]:
    if not artifact:
        return set()
    values = artifact.get(field, [])
    if not isinstance(values, list):
        return set()
    return {
        value["key"]
        for value in values
        if isinstance(value, dict) and isinstance(value.get("key"), str)
    }


def _artifact_block_keys(artifact: dict[str, Any] | None) -> set[str]:
    keys: set[str] = set()
    for field in ("sections", "formulas", "worked_examples", "labs", "exercises"):
        keys.update(_artifact_keys(artifact, field))
    return keys


async def _owned_chapter(
    *,
    course_id: str,
    version_id: str,
    chapter_id: str | None = None,
    chapter_key: str | None = None,
) -> Chapter:
    version = await _typed_get(CourseVersion, version_id, "course_version")
    if version.course != course_id:
        raise NotFoundError("Course resource not found")
    if chapter_id is not None:
        chapter = await _typed_get(Chapter, chapter_id, "chapter")
        if chapter.course_version != version_id:
            raise NotFoundError("Course resource not found")
        if chapter_key is not None and chapter.chapter_key != chapter_key:
            raise NotFoundError("Course resource not found")
        return chapter
    if chapter_key is None:
        raise NotFoundError("Course resource not found")
    chapters = await CourseVersion.chapters(version_id)
    matches = [chapter for chapter in chapters if chapter.chapter_key == chapter_key]
    if not matches:
        raise NotFoundError("Course resource not found")
    return max(matches, key=lambda chapter: chapter.version_no)


class CourseService:
    @staticmethod
    async def get_model_options() -> dict[str, Any]:
        """Return explicit Course-only selections without changing global defaults."""
        configured_models = await Model.get_models_by_type("language")
        efforts = ["low", "medium", "high", "xhigh", "max"]
        options: list[dict[str, Any]] = [
            {
                "adapter": "codex_cli",
                "model": model,
                "reasoning_effort": "max",
                "reasoning_efforts": efforts,
                "optional": False,
                "configured": True,
            }
            for model in ("gpt-5.6-sol", "gpt-5.6-luna")
        ]
        options.extend(
            {
                "adapter": "ollama",
                "model": model,
                "reasoning_effort": None,
                "optional": True,
                "configured": False,
            }
            for model in ("qwen3.5:9b", "deepseek-r1:8b")
        )
        options.extend(
            {
                "adapter": "open_notebook",
                "model": str(model.id),
                "reasoning_effort": None,
                "optional": False,
                "configured": True,
                "name": model.name,
                "provider": model.provider,
            }
            for model in sorted(
                configured_models,
                key=lambda item: (item.provider, item.name, str(item.id)),
            )
        )
        deepseek_configured = any(
            model.provider == "deepseek" and model.name == "deepseek-v4-pro"
            for model in configured_models
        )
        options.append(
            {
                "adapter": "open_notebook",
                "model": "deepseek-v4-pro",
                "reasoning_effort": None,
                "optional": True,
                "configured": deepseek_configured,
            }
        )
        return {
            "defaults": {
                stage: selection.model_dump(mode="json")
                for stage, selection in DEFAULT_MODEL_POLICY.items()
            },
            "options": options,
        }

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
        notebook = await _typed_get(Notebook, course.notebook, "notebook")
        source = await _typed_get(Source, source_id, "source")
        if source_id in course.source_ids:
            existing_role = (
                "PRIMARY" if source_id in course.primary_source_ids else "SUPPLEMENT"
            )
            raise CourseConflictError(
                f"Source is already associated as {existing_role}"
            )
        notebook_sources = await notebook.get_sources()
        relationship_created = source_id not in {
            item.id for item in notebook_sources
        }
        if relationship_created:
            await source.add_to_notebook(course.notebook)
        course.source_ids.append(source_id)
        if role == "PRIMARY":
            course.primary_source_ids.append(source_id)
        else:
            course.supplement_source_ids.append(source_id)
        try:
            await course.save()
        except Exception:
            course.source_ids.remove(source_id)
            if role == "PRIMARY":
                course.primary_source_ids.remove(source_id)
            else:
                course.supplement_source_ids.remove(source_id)
            if relationship_created:
                await repo_query(
                    "DELETE reference WHERE in = $source_id AND out = $notebook_id",
                    {
                        "source_id": ensure_record_id(source_id),
                        "notebook_id": ensure_record_id(course.notebook),
                    },
                )
            raise
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
        if version.status in {sm.VersionStatus.PUBLISHED, sm.VersionStatus.FAILED}:
            raise CourseConflictError("Course version cannot be approved in its current state")
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
        if version.status != sm.VersionStatus.GENERATING:
            raise CourseConflictError("Course version is not ready for publication")
        course = await CourseService.get_course(version.course)
        if (
            course.outline_version_id != version_id
            or version.approved_at is None
            or version.outline_artifact is None
            or version.outline_hash != _artifact_hash(version.outline_artifact)
        ):
            raise CourseConflictError("Approved outline hash does not match")
        chapters = await CourseVersion.chapters(version_id)
        if not chapters or any(
            chapter.status != sm.ChapterStatus.PUBLISHED for chapter in chapters
        ):
            raise CourseConflictError("All chapters must be published first")
        version.status = sm.transition(
            "version", version.status, sm.VersionStatus.PUBLISHED
        )
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
        requested_chapter_key = values.get("chapter_key")
        requested_exercise_key = values.get("exercise_key")
        chapter: Chapter | None = None
        if lab.chapter is not None or requested_chapter_key is not None:
            chapter = await _owned_chapter(
                course_id=version.course,
                version_id=lab.course_version,
                chapter_id=lab.chapter,
                chapter_key=requested_chapter_key,
            )
        if requested_exercise_key is not None:
            if chapter is None or requested_exercise_key not in _artifact_keys(
                chapter.artifact, "exercises"
            ):
                raise NotFoundError("Course resource not found")
        attempt = Attempt(
            lab=lab_id,
            answers=values["answers"],
            course=version.course,
            course_version=lab.course_version,
            chapter=chapter.id if chapter is not None else lab.chapter,
            chapter_key=chapter.chapter_key if chapter is not None else None,
            exercise_key=requested_exercise_key,
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
        version = await _typed_get(CourseVersion, lab.course_version, "course_version")
        if attempt.course and attempt.course != version.course:
            raise NotFoundError("Attempt ownership mismatch")
        if attempt.course_version and attempt.course_version != lab.course_version:
            raise NotFoundError("Attempt ownership mismatch")
        if attempt.chapter and lab.chapter and attempt.chapter != lab.chapter:
            raise NotFoundError("Attempt ownership mismatch")
        chapter_id = attempt.chapter or lab.chapter
        if chapter_id or attempt.chapter_key or attempt.exercise_key:
            chapter = await _owned_chapter(
                course_id=version.course,
                version_id=lab.course_version,
                chapter_id=chapter_id,
                chapter_key=attempt.chapter_key,
            )
            if attempt.exercise_key and attempt.exercise_key not in _artifact_keys(
                chapter.artifact, "exercises"
            ):
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
        course = await CourseService.get_course(course_id)
        chapter_id = values.get("chapter")
        chapter_key = values.get("chapter_key")
        block_key = values.get("block_key")
        chapter: Chapter | None = None
        if chapter_id or chapter_key or block_key:
            if not course.outline_version_id:
                raise NotFoundError("Course resource not found")
            chapter = await _owned_chapter(
                course_id=course_id,
                version_id=course.outline_version_id,
                chapter_id=chapter_id,
                chapter_key=chapter_key,
            )
            chapter_id = chapter.id
            chapter_key = chapter.chapter_key
        result = await repo_query(
            "SELECT * FROM progress WHERE course = $course AND chapter_key = $chapter_key",
            {"course": ensure_record_id(course_id), "chapter_key": chapter_key},
        )
        progress = Progress(**result[0]) if result else Progress(
            course=course_id,
            chapter=chapter_id,
            chapter_key=chapter_key,
            block_key=block_key,
            orphan_status=(
                "orphaned"
                if block_key and block_key not in _artifact_block_keys(chapter.artifact if chapter else None)
                else "active"
            ),
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
        course = await CourseService.get_course(course_id)
        chapter_id = values.get("chapter")
        chapter_key = values.get("chapter_key")
        block_key = values.get("block_key")
        payload = dict(values)
        if chapter_id or chapter_key or block_key:
            if not course.outline_version_id:
                raise NotFoundError("Course resource not found")
            chapter = await _owned_chapter(
                course_id=course_id,
                version_id=course.outline_version_id,
                chapter_id=chapter_id,
                chapter_key=chapter_key,
            )
            payload["chapter"] = chapter.id
            payload["chapter_key"] = chapter.chapter_key
            payload["orphan_status"] = (
                "orphaned"
                if block_key and block_key not in _artifact_block_keys(chapter.artifact)
                else "active"
            )
        note = CourseNote(course=course_id, **payload)
        await note.save()
        return note

    @staticmethod
    async def delete_note(course_id: str, note_id: str) -> None:
        await CourseService.get_course(course_id)
        note = await _typed_get(CourseNote, note_id, "course_note")
        if note.course != course_id:
            raise NotFoundError("Course note not found")
        await note.delete()
