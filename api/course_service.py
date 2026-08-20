"""Course application services; routers contain no persistence decisions."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Literal, TypeVar
from uuid import uuid4

import httpx

from open_notebook.ai.models import Model
from open_notebook.course import state_machine as sm
from open_notebook.course.contracts import (
    ChapterArtifact,
    CourseOutlineArtifact,
    ValidationFinding,
)
from open_notebook.course.evidence_service import EvidenceInputError, EvidenceService
from open_notebook.course.generation_service import (
    CourseGenerationService,
    PublicationBlocked,
)
from open_notebook.course.model_adapters import CodexCliAdapter
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
from open_notebook.course.workflow_service import (
    CourseWorkflowService,
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


def _extend_unique_anchor_ids(
    target: list[str], seen: set[str], anchor_ids: list[str]
) -> None:
    """Append anchors once while preserving their first contract-defined position."""

    for anchor_id in anchor_ids:
        if anchor_id not in seen:
            seen.add(anchor_id)
            target.append(anchor_id)


def _chapter_artifact_anchor_ids(artifact: ChapterArtifact) -> list[str]:
    """Collect every evidence-bearing ChapterArtifact field in stable order."""

    collected: list[str] = []
    seen: set[str] = set()
    _extend_unique_anchor_ids(collected, seen, artifact.citations)
    _extend_unique_anchor_ids(
        collected, seen, artifact.attributions.purpose.anchor_ids
    )
    for field_name in (
        "prerequisites",
        "objectives",
        "definitions",
        "misconceptions",
        "pitfalls",
        "quick_reference",
    ):
        for attribution in getattr(artifact.attributions, field_name):
            _extend_unique_anchor_ids(collected, seen, attribution.anchor_ids)
    for items in (
        artifact.sections,
        artifact.formulas,
        artifact.worked_examples,
        artifact.labs,
        artifact.exercises,
        artifact.physics_checks,
    ):
        for item in items:
            _extend_unique_anchor_ids(collected, seen, item.anchor_ids)
    return collected


def _generated_chapter_artifact(chapter: Chapter) -> ChapterArtifact:
    """Fail closed when a publication candidate is manual or malformed."""

    if chapter.input_hash is None or chapter.artifact is None:
        raise CourseConflictError(
            "Chapter artifact is missing or invalid for generated publication"
        )
    try:
        artifact = ChapterArtifact.model_validate(chapter.artifact)
    except (TypeError, ValueError) as exc:
        raise CourseConflictError(
            "Chapter artifact is missing or invalid for generated publication"
        ) from exc
    if artifact.chapter_key != chapter.chapter_key:
        raise CourseConflictError(
            "Chapter artifact is missing or invalid for generated publication"
        )
    return artifact


def _publishable_findings(rows: list[dict[str, Any]]) -> list[ValidationFinding]:
    try:
        findings = [ValidationFinding.model_validate(row["finding"]) for row in rows]
        CourseGenerationService.assert_publishable(findings)
    except (KeyError, TypeError, ValueError, PublicationBlocked) as exc:
        raise CourseConflictError(
            "Chapter has unresolved validation findings"
        ) from exc
    return sorted(
        findings,
        key=lambda finding: (
            finding.item_key,
            finding.kind,
            finding.severity,
            finding.status,
            finding.message,
        ),
    )


def _publication_anchor_ids(
    *,
    outline: CourseOutlineArtifact,
    artifacts: list[ChapterArtifact],
    findings: list[ValidationFinding],
    chapter_keys: list[str],
    include_concepts: bool,
) -> list[str]:
    collected: list[str] = []
    seen: set[str] = set()
    chapters_by_key = {chapter.key: chapter for chapter in outline.chapters}
    for chapter_key in chapter_keys:
        proposal = chapters_by_key[chapter_key]
        _extend_unique_anchor_ids(collected, seen, proposal.anchor_ids)
    if include_concepts:
        for concept in outline.concepts:
            _extend_unique_anchor_ids(collected, seen, concept.anchor_ids)
    for artifact in artifacts:
        _extend_unique_anchor_ids(
            collected, seen, _chapter_artifact_anchor_ids(artifact)
        )
    for finding in findings:
        _extend_unique_anchor_ids(collected, seen, finding.anchor_ids)
    return collected


async def _revalidate_publication_evidence(
    *,
    course_id: str,
    version: CourseVersion,
    outline: CourseOutlineArtifact,
    anchor_ids: list[str],
) -> Course:
    """Reload Course ownership and use owned Source assets at the commit boundary."""

    current_course = await CourseService.get_course(course_id)
    try:
        CourseWorkflowService.validate_approved_version(current_course, version)
        await CourseWorkflowService().grounded_inputs(
            course=current_course, anchor_ids=anchor_ids
        )
    except (EvidenceInputError, ValueError) as exc:
        raise CourseConflictError(
            "Evidence changed; rebuild evidence before publication"
        ) from exc
    return current_course


async def _installed_ollama_models() -> set[str]:
    """Probe only the local Ollama registry; offline means unavailable."""

    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            response = await client.get("http://127.0.0.1:11434/api/tags")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, TypeError, ValueError):
        return set()
    models = payload.get("models", []) if isinstance(payload, dict) else []
    return {
        name
        for item in models
        if isinstance(item, dict)
        for name in (item.get("name") or item.get("model"),)
        if isinstance(name, str)
    }


async def _approved_outline_records(
    course_id: str,
) -> tuple[Course, CourseVersion, CourseOutlineArtifact]:
    course = await CourseService.get_course(course_id)
    if not course.outline_version_id:
        raise CourseConflictError("Current Course outline is not approved")
    version = await _typed_get(
        CourseVersion, course.outline_version_id, "course_version"
    )
    if str(version.id) != course.outline_version_id or version.course != course_id:
        raise NotFoundError("Course resource not found")
    try:
        outline = CourseWorkflowService.validate_approved_version(course, version)
    except ValueError as exc:
        raise CourseConflictError("Current Course outline is not approved") from exc
    return course, version, outline


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
    return await CourseWorkflowService.resolve_current_chapter(
        course_id=course_id,
        version_id=version_id,
        chapter_key=chapter_key,
        chapters=chapters,
    )


async def _current_chapter_records(
    course_id: str, chapter_key: str
) -> tuple[Course, CourseVersion, Chapter]:
    """Resolve the current approved Course version and latest stable-key chapter."""

    course, version, outline = await _approved_outline_records(course_id)
    try:
        CourseWorkflowService._outline_chapter(outline, chapter_key)
    except ValueError:
        raise NotFoundError("Course chapter not found") from None
    version_id = str(version.id)
    chapter = await _owned_chapter(
        course_id=course_id,
        version_id=version_id,
        chapter_key=chapter_key,
    )
    return course, version, chapter


def _persistent_lab_key(lab: Lab) -> str:
    value = lab.payload.get("key") if isinstance(lab.payload, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise CourseConflictError("Persistent Lab has no stable key")
    return value


async def _current_chapter_labs(
    course_id: str, chapter_key: str
) -> tuple[CourseVersion, Chapter, list[tuple[str, Lab]]]:
    _, version, chapter = await _current_chapter_records(course_id, chapter_key)
    if chapter.id is None:
        raise CourseConflictError("Current chapter is not persisted")
    version_id = str(version.id)
    chapter_id = str(chapter.id)
    keyed: list[tuple[str, Lab]] = []
    seen: set[str] = set()
    for lab in await CourseVersion.labs(version_id):
        if lab.course_version != version_id or lab.chapter != chapter_id:
            continue
        key = _persistent_lab_key(lab)
        if key in seen:
            raise CourseConflictError("Persistent Lab stable key is not unique")
        seen.add(key)
        keyed.append((key, lab))
    if seen != _artifact_keys(chapter.artifact, "labs"):
        raise CourseConflictError(
            "Chapter artifact and persistent Lab stable keys do not match"
        )
    return version, chapter, keyed


class CourseService:
    @staticmethod
    async def get_model_options() -> dict[str, Any]:
        """Return explicit Course-only selections without changing global defaults."""
        configured_models = await Model.get_models_by_type("language")
        real_models_enabled = os.getenv("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS") == "1"
        codex_available = real_models_enabled and CodexCliAdapter.is_available()
        installed_ollama = (
            await _installed_ollama_models() if real_models_enabled else set()
        )
        efforts = ["low", "medium", "high", "xhigh", "max"]
        options: list[dict[str, Any]] = [
            {
                "adapter": "codex_cli",
                "model": model,
                "reasoning_effort": "max",
                "reasoning_efforts": efforts,
                "optional": False,
                "configured": codex_available,
                "selectable": codex_available,
            }
            for model in ("gpt-5.6-sol", "gpt-5.6-luna")
        ]
        options.extend(
            {
                "adapter": "ollama",
                "model": model,
                "reasoning_effort": None,
                "optional": True,
                "configured": model in installed_ollama,
                "selectable": model in installed_ollama,
            }
            for model in ("qwen3.5:9b", "gpt-oss:20b")
        )
        options.extend(
            {
                "adapter": "open_notebook",
                "model": str(model.id),
                "reasoning_effort": None,
                "optional": model.provider == "deepseek",
                "configured": real_models_enabled,
                "selectable": real_models_enabled,
                "name": model.name,
                "provider": model.provider,
            }
            for model in sorted(
                configured_models,
                key=lambda item: (item.provider, item.name, str(item.id)),
            )
            if model.id is not None
        )
        deepseek_configured = any(
            model.provider == "deepseek" and model.name == "deepseek-v4-pro"
            for model in configured_models
        )
        if not deepseek_configured:
            options.append(
                {
                    "adapter": "open_notebook",
                    "model": None,
                    "display_name": "deepseek-v4-pro",
                    "reasoning_effort": None,
                    "optional": True,
                    "configured": False,
                    "selectable": False,
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
            created_notebook = Notebook(name=title, description=f"STEM Course: {title}")
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
        if role not in {"PRIMARY", "SUPPLEMENT"}:
            raise InvalidInputError("Course Source role is invalid")
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
        if source_id not in {str(item.id) for item in notebook_sources}:
            raise CourseConflictError("Source is not attached to this Course notebook")
        file_path = source.asset.file_path if source.asset else None
        if not file_path:
            raise EvidenceInputError("Course Source has no local PDF or PPTX asset.")
        evidence = EvidenceService()
        evidence.validate_local_source_file(file_path)

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
            raise
        return course

    @staticmethod
    async def create_version(course_id: str, values: dict[str, Any]) -> CourseVersion:
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
            raise CourseConflictError(
                "Course version cannot be approved in its current state"
            )
        if version.outline_artifact is None:
            raise CourseConflictError("Outline version has no artifact")
        version.confirmation = normalized
        version.approved_at = datetime.now(timezone.utc)
        version.outline_hash = _artifact_hash(version.outline_artifact)
        course.status = sm.transition(
            "course", course.status, sm.CourseStatus.OUTLINE_APPROVED
        )
        await version.save()
        await course.save()
        return version

    @staticmethod
    async def create_chapter(version_id: str, values: dict[str, Any]) -> Chapter:
        version = await _typed_get(CourseVersion, version_id, "course_version")
        mutable_statuses = {
            sm.VersionStatus.DRAFT,
            sm.VersionStatus.GENERATING,
        }
        if version.status not in mutable_statuses:
            raise CourseImmutableError("Course version is immutable")
        chapter_key = values.get("chapter_key") or f"chapter-{values['chapter_no']}"
        chapter = Chapter(
            course_version=version_id,
            chapter_no=values["chapter_no"],
            title=values["title"],
            chapter_key=chapter_key,
            artifact=values.get("artifact"),
            input_hash=values.get("input_hash"),
        )
        chapter_id = f"chapter:{uuid4().hex}"
        try:
            await repo_query(
                """
                BEGIN TRANSACTION;
                LET $mutable_version = (
                    UPDATE course_version
                    SET updated = time::now()
                    WHERE id = $version_id
                      AND status IN ['draft', 'generating']
                    RETURN VALUE id
                );
                IF array::len($mutable_version) != 1 {
                    THROW 'Course version is immutable'
                };
                LET $existing_version_nos = (
                    SELECT VALUE version_no FROM chapter
                    WHERE course_version = $version_id
                      AND chapter_key = $chapter_key
                    ORDER BY version_no DESC
                    LIMIT 1
                );
                LET $next_version_no = IF array::len($existing_version_nos) = 0 {
                    1
                } ELSE {
                    $existing_version_nos[0] + 1
                };
                LET $created_chapter = (
                    CREATE ONLY $chapter_id
                    SET course_version = $version_id,
                        chapter_no = $chapter_no,
                        title = $title,
                        chapter_key = $chapter_key,
                        version_no = $next_version_no,
                        artifact = $artifact,
                        input_hash = $input_hash,
                        status = 'draft',
                        review_status = 'pending',
                        validation_status = 'pending',
                        created = time::now(),
                        updated = time::now()
                    RETURN AFTER
                );
                IF $created_chapter = NONE {
                    THROW 'Chapter creation failed'
                };
                COMMIT TRANSACTION;
                """,
                {
                    "version_id": ensure_record_id(version_id),
                    "chapter_id": ensure_record_id(chapter_id),
                    "chapter_no": chapter.chapter_no,
                    "title": chapter.title,
                    "chapter_key": chapter.chapter_key,
                    "artifact": chapter.artifact,
                    "input_hash": chapter.input_hash,
                },
            )
        except RuntimeError as exc:
            current = await _typed_get(CourseVersion, version_id, "course_version")
            if current.status not in mutable_statuses:
                raise CourseImmutableError("Course version is immutable") from exc
            raise CourseConflictError(
                "Chapter creation conflicted with another Course update"
            ) from exc
        return await _typed_get(Chapter, chapter_id, "chapter")

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
        mutable_statuses = {
            sm.VersionStatus.DRAFT,
            sm.VersionStatus.GENERATING,
        }
        if (
            version.status not in mutable_statuses
            or chapter.status == sm.ChapterStatus.PUBLISHED
        ):
            raise CourseImmutableError("Course artifact is immutable")

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

        updated = chapter.model_copy(deep=True)
        for field in ("title", "content", "citations", "artifact", "input_hash"):
            if field in values and values[field] is not None:
                setattr(updated, field, values[field])
        updated.status = next_status
        updated.review_status = next_review
        updated.validation_status = next_validation
        # Validate the complete candidate before entering the transaction so a
        # rejected patch cannot partially mutate either memory or persistence.
        updated = Chapter.model_validate(updated.model_dump())
        try:
            await repo_query(
                """
                BEGIN TRANSACTION;
                LET $mutable_version = (
                    UPDATE course_version
                    SET updated = time::now()
                    WHERE id = $version_id
                      AND status IN ['draft', 'generating']
                      AND updated = $expected_version_updated
                    RETURN VALUE id
                );
                IF array::len($mutable_version) != 1 {
                    THROW 'Course version is immutable'
                };
                LET $updated_chapter = (
                    UPDATE $chapter_id
                    SET title = $title,
                        content = $content,
                        citations = $citations,
                        artifact = $artifact,
                        input_hash = $input_hash,
                        status = $status,
                        review_status = $review_status,
                        validation_status = $validation_status,
                        updated = time::now()
                    WHERE course_version = $version_id
                      AND status != 'published'
                    RETURN AFTER
                );
                IF array::len($updated_chapter) != 1 {
                    THROW 'Chapter is immutable or stale'
                };
                COMMIT TRANSACTION;
                """,
                {
                    "version_id": ensure_record_id(version_id),
                    "expected_version_updated": version.updated,
                    "chapter_id": ensure_record_id(chapter_id),
                    "title": updated.title,
                    "content": updated.content,
                    "citations": updated.citations,
                    "artifact": updated.artifact,
                    "input_hash": updated.input_hash,
                    "status": updated.status,
                    "review_status": updated.review_status,
                    "validation_status": updated.validation_status,
                },
            )
        except RuntimeError as exc:
            current_version = await _typed_get(
                CourseVersion, version_id, "course_version"
            )
            if current_version.status not in mutable_statuses:
                raise CourseImmutableError("Course version is immutable") from exc
            current_chapter = await _typed_get(Chapter, chapter_id, "chapter")
            if current_chapter.status == sm.ChapterStatus.PUBLISHED:
                raise CourseImmutableError("Course artifact is immutable") from exc
            raise CourseConflictError(
                "Chapter update conflicted with another Course update"
            ) from exc
        return await _typed_get(Chapter, chapter_id, "chapter")

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
        try:
            outline = CourseWorkflowService.validate_approved_version(course, version)
            CourseWorkflowService._outline_chapter(outline, chapter.chapter_key)
        except ValueError as exc:
            raise CourseConflictError("Current Course outline is not approved") from exc
        if chapter.status != sm.ChapterStatus.READY:
            raise CourseConflictError("Chapter is not ready for publication")
        if (
            chapter.review_status != sm.ChapterReviewStatus.PASSED
            or chapter.validation_status != sm.ChapterValidationStatus.PASSED
        ):
            raise CourseConflictError("Chapter review and validation must pass")
        rows = await repo_query(
            "SELECT * FROM course_validation_finding "
            "WHERE course = $course AND course_version = $version "
            "AND chapter = $chapter;",
            {
                "course": ensure_record_id(course_id),
                "version": ensure_record_id(version_id),
                "chapter": ensure_record_id(chapter_id),
            },
        )
        findings = _publishable_findings(rows)
        artifact = _generated_chapter_artifact(chapter)
        anchor_ids = _publication_anchor_ids(
            outline=outline,
            artifacts=[artifact],
            findings=findings,
            chapter_keys=[chapter.chapter_key],
            include_concepts=False,
        )
        await _revalidate_publication_evidence(
            course_id=course_id,
            version=version,
            outline=outline,
            anchor_ids=anchor_ids,
        )
        chapter.status = sm.transition(
            "chapter", chapter.status, sm.ChapterStatus.PUBLISHED
        )
        chapter.published_at = datetime.now(timezone.utc)
        await chapter.save()
        return chapter

    @staticmethod
    async def publish_current_chapter(course_id: str, chapter_key: str) -> Chapter:
        """Publish the latest chapter from the current approved Course version."""

        _, version, chapter = await _current_chapter_records(course_id, chapter_key)
        return await CourseService.publish_chapter(
            course_id, str(version.id), str(chapter.id)
        )

    @staticmethod
    async def publish_version(version_id: str) -> CourseVersion:
        version = await _typed_get(CourseVersion, version_id, "course_version")
        if version.status not in {
            sm.VersionStatus.GENERATING,
            sm.VersionStatus.PUBLISHED,
        }:
            raise CourseConflictError("Course version is not ready for publication")
        course = await CourseService.get_course(version.course)
        try:
            outline = CourseWorkflowService.validate_approved_version(course, version)
        except ValueError as exc:
            raise CourseConflictError("Approved outline hash does not match") from exc
        chapters = await CourseVersion.chapters(version_id)
        expected_keys = {proposal.key for proposal in outline.chapters}
        promotions = []
        for key in sorted(expected_keys):
            promotion = await CourseWorkflowService.chapter_promotion_snapshot(
                course_id=version.course,
                version_id=version_id,
                chapter_key=key,
                chapters=chapters,
            )
            if promotion.current is None:
                raise CourseConflictError("All chapters must be published first")
            promotions.append(promotion)
        latest = [
            promotion.current
            for promotion in promotions
            if promotion.current is not None
        ]
        if len(latest) != len(expected_keys) or any(
            chapter.status != sm.ChapterStatus.PUBLISHED for chapter in latest
        ):
            raise CourseConflictError("All chapters must be published first")

        artifacts: list[ChapterArtifact] = []
        findings: list[ValidationFinding] = []
        for chapter in latest:
            artifacts.append(_generated_chapter_artifact(chapter))
            rows = await repo_query(
                "SELECT * FROM course_validation_finding "
                "WHERE course = $course AND course_version = $version "
                "AND chapter = $chapter;",
                {
                    "course": ensure_record_id(version.course),
                    "version": ensure_record_id(version_id),
                    "chapter": ensure_record_id(str(chapter.id)),
                },
            )
            findings.extend(_publishable_findings(rows))

        known_succeeded_run_ids = sorted(
            {
                run_id
                for promotion in promotions
                for run_id in promotion.succeeded_run_ids
            }
        )
        known_manual_chapter_ids = sorted(
            {
                chapter_id
                for promotion in promotions
                for chapter_id in promotion.manual_chapter_ids
            }
        )

        if course.status not in {
            sm.CourseStatus.GENERATING,
            sm.CourseStatus.READY,
        }:
            raise CourseConflictError("Course is no longer generating")

        published_at = version.published_at or datetime.now(timezone.utc)
        next_version_status = version.status
        if next_version_status == sm.VersionStatus.GENERATING:
            next_version_status = sm.transition(
                "version", version.status, sm.VersionStatus.PUBLISHED
            )
        next_course_status = course.status
        if next_course_status == sm.CourseStatus.GENERATING:
            next_course_status = sm.transition(
                "course", course.status, sm.CourseStatus.READY
            )

        anchor_ids = _publication_anchor_ids(
            outline=outline,
            artifacts=artifacts,
            findings=findings,
            chapter_keys=sorted(expected_keys),
            include_concepts=True,
        )
        await _revalidate_publication_evidence(
            course_id=version.course,
            version=version,
            outline=outline,
            anchor_ids=anchor_ids,
        )

        try:
            await repo_query(
                """
                BEGIN TRANSACTION;
                LET $version_update = (
                    UPDATE course_version
                    SET status = 'published', published_at = $published_at
                    WHERE id = $version_id
                      AND course = $course_id
                      AND status = 'generating'
                      AND outline_hash = $outline_hash
                      AND updated = $expected_version_updated
                    RETURN AFTER
                );
                LET $published_current_chapters = (
                    SELECT VALUE id FROM chapter
                    WHERE id IN $current_chapter_ids
                      AND course_version = $version_id
                      AND chapter_key IN $expected_chapter_keys
                      AND status = 'published'
                );
                IF array::len($published_current_chapters)
                   != $expected_chapter_count {
                    THROW 'Course publication chapter snapshot changed'
                };
                LET $unexpected_succeeded_runs = (
                    SELECT VALUE id FROM course_generation_run
                    WHERE course = $course_id
                      AND course_version = $version_id
                      AND chapter_key IN $expected_chapter_keys
                      AND stage = 'chapter_content'
                      AND status = 'succeeded'
                      AND id NOT IN $known_succeeded_run_ids
                );
                IF array::len($unexpected_succeeded_runs) != 0 {
                    THROW 'Course publication chapter snapshot changed'
                };
                LET $unexpected_manual_chapters = (
                    SELECT VALUE id FROM chapter
                    WHERE course_version = $version_id
                      AND chapter_key IN $expected_chapter_keys
                      AND input_hash = NONE
                      AND id NOT IN $known_manual_chapter_ids
                );
                IF array::len($unexpected_manual_chapters) != 0 {
                    THROW 'Course publication chapter snapshot changed'
                };
                LET $course_update = (
                    UPDATE course
                    SET status = 'ready'
                    WHERE id = $course_id
                      AND status = 'generating'
                      AND outline_version_id = $version_id
                    RETURN AFTER
                );
                LET $published_version = (
                    SELECT id FROM course_version
                    WHERE id = $version_id
                      AND course = $course_id
                      AND status = 'published'
                      AND outline_hash = $outline_hash
                );
                LET $ready_course = (
                    SELECT id FROM course
                    WHERE id = $course_id
                      AND status = 'ready'
                      AND outline_version_id = $version_id
                );
                IF array::len($published_version) != 1
                   OR array::len($ready_course) != 1 {
                    THROW 'Course publication conflict'
                };
                COMMIT TRANSACTION;
                """,
                {
                    "course_id": ensure_record_id(str(course.id)),
                    "version_id": ensure_record_id(version_id),
                    "outline_hash": version.outline_hash,
                    "expected_version_updated": version.updated,
                    "published_at": published_at,
                    "expected_chapter_keys": sorted(expected_keys),
                    "expected_chapter_count": len(expected_keys),
                    "current_chapter_ids": [
                        ensure_record_id(str(chapter.id)) for chapter in latest
                    ],
                    "known_succeeded_run_ids": [
                        ensure_record_id(run_id) for run_id in known_succeeded_run_ids
                    ],
                    "known_manual_chapter_ids": [
                        ensure_record_id(chapter_id)
                        for chapter_id in known_manual_chapter_ids
                    ],
                },
            )
        except RuntimeError as exc:
            current_version = await _typed_get(
                CourseVersion, version_id, "course_version"
            )
            current_course = await CourseService.get_course(version.course)
            if (
                current_version.status == sm.VersionStatus.PUBLISHED
                and current_version.course == version.course
                and current_version.outline_hash == version.outline_hash
                and current_course.status == sm.CourseStatus.READY
                and current_course.outline_version_id == version_id
            ):
                return current_version
            raise CourseConflictError("Course is no longer generating") from exc

        version.status = next_version_status
        version.published_at = published_at
        course.status = next_course_status
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
    async def list_chapter_labs(
        course_id: str, chapter_key: str
    ) -> list[dict[str, Any]]:
        """Expose current persistent Labs through stable artifact keys."""

        _, _, labs = await _current_chapter_labs(course_id, chapter_key)
        result: list[dict[str, Any]] = []
        for lab_key, lab in labs:
            if lab.id is None:
                raise CourseConflictError("Current Lab is not persisted")
            result.append(
                {
                    "id": str(lab.id),
                    "lab_key": lab_key,
                    "lab_type": lab.lab_type,
                    "spec": lab.payload,
                }
            )
        return result

    @staticmethod
    async def create_chapter_attempt(
        course_id: str,
        chapter_key: str,
        lab_key: str,
        values: dict[str, Any],
    ) -> Attempt:
        """Resolve a current persistent Lab by stable key and save one attempt."""

        version, chapter, keyed_labs = await _current_chapter_labs(
            course_id, chapter_key
        )
        matches = [lab for key, lab in keyed_labs if key == lab_key]
        if len(matches) != 1 or matches[0].id is None or chapter.id is None:
            raise NotFoundError("Course Lab not found")
        exercise_key = values.get("exercise_key")
        if exercise_key is not None and exercise_key not in _artifact_keys(
            chapter.artifact, "exercises"
        ):
            raise NotFoundError("Course exercise not found")
        attempt = Attempt(
            lab=str(matches[0].id),
            answers=values["answers"],
            course=course_id,
            course_version=str(version.id),
            chapter=str(chapter.id),
            chapter_key=chapter.chapter_key,
            exercise_key=exercise_key,
            answer=values.get("answer"),
            hints_used=values.get("hints_used"),
            answer_revealed=values.get("answer_revealed"),
            transfer_completed=values.get("transfer_completed"),
            orphan_status="active",
        )
        await attempt.save()
        return attempt

    @staticmethod
    async def list_chapter_attempts(
        course_id: str, chapter_key: str
    ) -> list[dict[str, Any]]:
        """Return current and historical attempts with explicit stable Lab keys."""

        await _current_chapter_records(course_id, chapter_key)
        rows = await repo_query(
            """
            SELECT * FROM attempt
            WHERE course = $course AND chapter_key = $chapter_key
            ORDER BY created DESC;
            """,
            {
                "course": ensure_record_id(course_id),
                "chapter_key": chapter_key,
            },
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            attempt = Attempt(**row)
            if attempt.course != course_id or attempt.chapter_key != chapter_key:
                raise NotFoundError("Course attempt ownership mismatch")
            lab = await _typed_get(Lab, attempt.lab, "lab")
            version = await _typed_get(
                CourseVersion, lab.course_version, "course_version"
            )
            if lab.chapter is None:
                raise NotFoundError("Course attempt ownership mismatch")
            historical_chapter = await _typed_get(Chapter, lab.chapter, "chapter")
            lab_key = _persistent_lab_key(lab)
            if (
                str(lab.id) != attempt.lab
                or str(version.id) != lab.course_version
                or version.course != course_id
                or str(historical_chapter.id) != lab.chapter
                or historical_chapter.course_version != lab.course_version
                or historical_chapter.chapter_key != chapter_key
                or lab_key not in _artifact_keys(historical_chapter.artifact, "labs")
                or (
                    attempt.exercise_key is not None
                    and attempt.exercise_key
                    not in _artifact_keys(historical_chapter.artifact, "exercises")
                )
                or (
                    attempt.course_version is not None
                    and attempt.course_version != lab.course_version
                )
                or (
                    attempt.chapter is not None
                    and lab.chapter is not None
                    and attempt.chapter != lab.chapter
                )
            ):
                raise NotFoundError("Course attempt ownership mismatch")
            payload = attempt.model_dump(mode="json")
            payload["id"] = str(attempt.id) if attempt.id is not None else None
            result.append({"lab_key": lab_key, "attempt": payload})
        return result

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
        if "chapter" in values:
            raise InvalidInputError("Client chapter record IDs are not accepted")
        chapter_key = values.get("chapter_key")
        block_key = values.get("block_key")
        if block_key and not chapter_key:
            raise InvalidInputError("block_key requires chapter_key")
        chapter_id: str | None = None
        chapter: Chapter | None = None
        if chapter_key:
            _, _, chapter = await _current_chapter_records(course_id, chapter_key)
            if chapter.id is None:
                raise CourseConflictError("Current chapter is not persisted")
            chapter_id = str(chapter.id)
            chapter_key = chapter.chapter_key
        else:
            await CourseService.get_course(course_id)
        result = await repo_query(
            "SELECT * FROM progress WHERE course = $course AND chapter_key = $chapter_key",
            {"course": ensure_record_id(course_id), "chapter_key": chapter_key},
        )
        orphan_status = (
            "orphaned"
            if block_key
            and block_key
            not in _artifact_block_keys(chapter.artifact if chapter else None)
            else "active"
        )
        progress = Progress(**result[0]) if result else Progress(course=course_id)
        progress.chapter = chapter_id
        progress.chapter_key = chapter_key
        progress.block_key = block_key
        progress.orphan_status = orphan_status
        progress.status = sm.transition("progress", progress.status, values["status"])
        await progress.save()
        return progress

    @staticmethod
    async def list_notes(course_id: str) -> list[CourseNote]:
        await CourseService.get_course(course_id)
        return await CourseNote.list_by_course(course_id)

    @staticmethod
    async def create_note(course_id: str, values: dict[str, Any]) -> CourseNote:
        if "chapter" in values:
            raise InvalidInputError("Client chapter record IDs are not accepted")
        chapter_key = values.get("chapter_key")
        block_key = values.get("block_key")
        if block_key and not chapter_key:
            raise InvalidInputError("block_key requires chapter_key")
        payload = dict(values)
        if chapter_key:
            _, _, chapter = await _current_chapter_records(course_id, chapter_key)
            if chapter.id is None:
                raise CourseConflictError("Current chapter is not persisted")
            payload["chapter"] = str(chapter.id)
            payload["chapter_key"] = chapter.chapter_key
            payload["orphan_status"] = (
                "orphaned"
                if block_key and block_key not in _artifact_block_keys(chapter.artifact)
                else "active"
            )
        else:
            await CourseService.get_course(course_id)
        note = CourseNote(course=course_id, **payload)
        await note.save()
        return note

    @staticmethod
    async def reattach_note(
        course_id: str,
        note_id: str,
        *,
        chapter_key: str,
        block_key: str,
    ) -> CourseNote:
        """Attach an existing Course note to a real block in the current chapter."""

        _, _, chapter = await _current_chapter_records(course_id, chapter_key)
        note = await _typed_get(CourseNote, note_id, "course_note")
        if note.course != course_id:
            raise NotFoundError("Course note not found")
        if block_key not in _artifact_block_keys(chapter.artifact):
            raise NotFoundError("Course chapter block not found")
        if chapter.id is None:
            raise CourseConflictError("Current chapter is not persisted")
        note.chapter = str(chapter.id)
        note.chapter_key = chapter.chapter_key
        note.block_key = block_key
        note.orphan_status = "active"
        await note.save()
        return note

    @staticmethod
    async def delete_note(course_id: str, note_id: str) -> None:
        await CourseService.get_course(course_id)
        note = await _typed_get(CourseNote, note_id, "course_note")
        if note.course != course_id:
            raise NotFoundError("Course note not found")
        await note.delete()
