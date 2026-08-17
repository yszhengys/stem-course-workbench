"""Replay-safe worker-side orchestration for Course generation records."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import Source
from open_notebook.exceptions import NotFoundError

from . import state_machine as sm
from .contracts import (
    ChapterArtifact,
    CourseOutlineArtifact,
    ModelSelection,
    ValidationFinding,
)
from .evidence_service import EvidenceInputError, EvidenceService
from .generation_service import CourseGenerationService, PublicationBlocked
from .locking import course_job_lock
from .models import (
    Chapter,
    Course,
    CourseEvidenceAnchor,
    CourseGenerationRun,
    CourseVersion,
    Lab,
)


def _artifact_hash(artifact: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            artifact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _rows(model: type[Any], result: Any) -> list[Any]:
    values = result if isinstance(result, list) else [result] if result else []
    return [model(**row) for row in values if isinstance(row, dict)]


class CourseWorkflowService:
    """All model calls enter here after reloading authoritative records."""

    def __init__(
        self,
        *,
        generation: CourseGenerationService | None = None,
        evidence: EvidenceService | None = None,
    ) -> None:
        self.generation = generation or CourseGenerationService()
        self.evidence = evidence or EvidenceService()

    async def load_run(
        self,
        *,
        run_id: str,
        course_id: str,
        stage: str,
        command_id: str | None,
    ) -> CourseGenerationRun:
        run = await CourseGenerationRun.get(run_id)
        if run.course != course_id or run.stage != stage:
            raise ValueError("Course generation run ownership mismatch")
        if not command_id or run.command != command_id:
            raise ValueError("Course generation run command binding mismatch")
        if run.status == sm.RunStatus.QUEUED:
            await run.transition_to(sm.RunStatus.RUNNING)
        elif run.status not in {sm.RunStatus.RUNNING, sm.RunStatus.SUCCEEDED}:
            raise ValueError("Course generation run is terminal")
        return run

    @staticmethod
    def validate_run_request(
        run: CourseGenerationRun,
        *,
        model: ModelSelection,
        prompt_version: str,
        chapter_key: str | None = None,
    ) -> None:
        if (
            run.adapter != model.adapter
            or run.model != model.model
            or run.reasoning_effort != model.reasoning_effort
            or run.prompt_version != prompt_version
            or run.chapter_key != chapter_key
        ):
            raise ValueError("Course generation run request does not match its claim")

    @staticmethod
    async def complete_run(
        run: CourseGenerationRun, output: dict[str, Any] | list[Any]
    ) -> None:
        run.output_hash = _artifact_hash({"output": output})
        if run.status == sm.RunStatus.RUNNING:
            await run.transition_to(sm.RunStatus.SUCCEEDED)

    @staticmethod
    async def fail_run(run: CourseGenerationRun, message: str) -> None:
        if run.status == sm.RunStatus.RUNNING:
            run.error_message = message[:1000]
            await run.transition_to(sm.RunStatus.FAILED)

    async def _source_hash(self, source_id: str) -> str:
        source = await Source.get(source_id)
        path = source.asset.file_path if source.asset else None
        if not path:
            raise EvidenceInputError("Course Source has no local PDF or PPTX asset.")
        safe_path = self.evidence.resolve_safe_source_path(path)
        self.evidence.validate_extension(safe_path)
        return self.evidence.sha256_file(safe_path)

    async def grounded_inputs(
        self, *, course: Course, anchor_ids: list[str]
    ) -> tuple[list[CourseEvidenceAnchor], dict[str, str], list[str]]:
        if not anchor_ids or len(anchor_ids) != len(set(anchor_ids)):
            raise EvidenceInputError("Evidence anchor IDs must be non-empty and unique.")
        result = await repo_query(
            """
            SELECT * FROM course_evidence_anchor
            WHERE course = $course_id AND is_current = true;
            """,
            {"course_id": ensure_record_id(str(course.id))},
        )
        anchors = _rows(CourseEvidenceAnchor, result)
        by_id = {anchor.anchor_id: anchor for anchor in anchors}
        selected: list[CourseEvidenceAnchor] = []
        source_hashes: dict[str, str] = {}
        for anchor_id in anchor_ids:
            anchor = by_id.get(anchor_id)
            if anchor is None:
                raise EvidenceInputError(f"Unknown or stale evidence anchor: {anchor_id}")
            if anchor.source not in course.source_ids:
                raise EvidenceInputError("Evidence Source is not associated with this Course.")
            selected.append(anchor)
            if anchor.source not in source_hashes:
                source_hashes[anchor.source] = await self._source_hash(anchor.source)
        context = self.generation.grounded_context(
            course_id=str(course.id),
            selected_anchor_ids=anchor_ids,
            anchors=selected,
            source_hashes=source_hashes,
        )
        return selected, source_hashes, context

    @staticmethod
    async def approved_version(
        course: Course,
    ) -> tuple[CourseVersion, CourseOutlineArtifact]:
        if not course.outline_version_id:
            raise ValueError("Course has no current outline version")
        version = await CourseVersion.get(course.outline_version_id)
        if version.course != course.id or version.approved_at is None:
            raise ValueError("Current outline is not approved")
        if version.confirmation != "确认大纲" or version.outline_artifact is None:
            raise ValueError("Current outline approval is invalid")
        if version.outline_hash != _artifact_hash(version.outline_artifact):
            raise ValueError("Approved outline hash changed")
        return version, CourseOutlineArtifact.model_validate(version.outline_artifact)

    async def build_evidence(
        self,
        *,
        run: CourseGenerationRun,
        course_id: str,
        source_id: str,
        role: Literal["PRIMARY", "SUPPLEMENT"],
    ) -> list[CourseEvidenceAnchor]:
        course = await Course.get(course_id)
        self.validate_run_request(
            run,
            model=ModelSelection(adapter="open_notebook", model="docling"),
            prompt_version="evidence-v1",
        )
        if source_id not in course.source_ids:
            raise ValueError("Source is not associated with this Course")
        anchors = await self.evidence.build(
            course_id=course_id,
            source_id=source_id,
            source_role=role,
        )
        if course.status in {sm.CourseStatus.DRAFT, sm.CourseStatus.FAILED}:
            course.status = sm.transition(
                "course", course.status, sm.CourseStatus.INDEXING
            )
            await course.save()
        await self.complete_run(run, [anchor.model_dump(mode="json") for anchor in anchors])
        return anchors

    async def generate_outline(
        self,
        *,
        run: CourseGenerationRun,
        course_id: str,
        anchor_ids: list[str],
        available_lab_keys: list[str],
        model: ModelSelection,
        prompt_version: str,
    ) -> CourseVersion:
        course = await Course.get(course_id)
        self.validate_run_request(
            run, model=model, prompt_version=prompt_version
        )
        if run.course_version != course.outline_version_id:
            current = (
                await CourseVersion.get(course.outline_version_id)
                if course.outline_version_id
                else None
            )
            if current is None or current.input_hash != run.input_hash:
                raise ValueError("Course outline generation run is stale")
        if course.status in {sm.CourseStatus.OUTLINE_APPROVED, sm.CourseStatus.GENERATING}:
            raise ValueError("The current approved outline cannot be overwritten")
        _, _, context = await self.grounded_inputs(course=course, anchor_ids=anchor_ids)
        existing = _rows(
            CourseVersion,
            await repo_query(
                "SELECT * FROM course_version WHERE course = $course AND input_hash = $hash;",
                {"course": ensure_record_id(course_id), "hash": run.input_hash},
            ),
        )
        if existing:
            version = existing[0]
        else:
            async with course_job_lock():
                artifact = await self.generation.generate_outline(
                    course_id=course_id,
                    anchor_ids=anchor_ids,
                    evidence=context,
                    available_lab_keys=set(available_lab_keys),
                    model=model,
                    prompt_version=prompt_version,
                )
            versions = await Course.versions(course_id)
            version = CourseVersion(
                course=course_id,
                version_no=max((item.version_no for item in versions), default=0) + 1,
                outline_artifact=artifact.model_dump(mode="json"),
                input_hash=run.input_hash,
            )
            await version.save()

        course.outline = version.outline_artifact
        course.outline_version_id = str(version.id)
        if course.status in {sm.CourseStatus.DRAFT, sm.CourseStatus.FAILED}:
            course.status = sm.transition("course", course.status, sm.CourseStatus.INDEXING)
        elif course.status == sm.CourseStatus.OUTLINE_READY:
            course.status = sm.transition("course", course.status, sm.CourseStatus.INDEXING)
        if course.status == sm.CourseStatus.INDEXING:
            course.status = sm.transition(
                "course", course.status, sm.CourseStatus.OUTLINE_READY
            )
        elif course.status == sm.CourseStatus.READY:
            course.status = sm.transition(
                "course", course.status, sm.CourseStatus.OUTLINE_READY
            )
        await course.save()
        await self.complete_run(run, version.outline_artifact or {})
        return version

    @staticmethod
    def _outline_chapter(
        outline: CourseOutlineArtifact, chapter_key: str
    ) -> tuple[int, Any]:
        for number, chapter in enumerate(outline.chapters, start=1):
            if chapter.key == chapter_key:
                return number, chapter
        raise ValueError("Chapter key is not in the approved outline")

    async def _ensure_labs(self, version: CourseVersion, chapter: Chapter) -> None:
        artifact = ChapterArtifact.model_validate(chapter.artifact)
        existing = await repo_query(
            "SELECT * FROM lab WHERE chapter = $chapter;",
            {"chapter": ensure_record_id(str(chapter.id))},
        )
        existing_keys = {
            row.get("payload", {}).get("key")
            for row in existing
            if isinstance(row.get("payload"), dict)
        }
        for spec in artifact.labs:
            if spec.key in existing_keys:
                continue
            lab = Lab(
                course_version=str(version.id),
                chapter=str(chapter.id),
                lab_type=spec.kind,
                payload=spec.model_dump(mode="json", by_alias=True),
            )
            await lab.save()

    @staticmethod
    async def _refresh_stable_links(
        *, course_id: str, chapter: Chapter, artifact: ChapterArtifact
    ) -> None:
        block_keys = {
            item.key
            for values in (
                artifact.sections,
                artifact.formulas,
                artifact.worked_examples,
                artifact.labs,
                artifact.exercises,
            )
            for item in values
        }
        exercise_keys = {item.key for item in artifact.exercises}
        await repo_query(
            """
            UPDATE course_note SET orphan_status =
                IF block_key != NONE AND block_key NOT IN $block_keys THEN 'orphaned' ELSE 'active' END
            WHERE course = $course AND chapter_key = $chapter_key;
            UPDATE progress SET orphan_status =
                IF block_key != NONE AND block_key NOT IN $block_keys THEN 'orphaned' ELSE 'active' END
            WHERE course = $course AND chapter_key = $chapter_key;
            UPDATE attempt SET orphan_status =
                IF exercise_key != NONE AND exercise_key NOT IN $exercise_keys THEN 'orphaned' ELSE 'active' END
            WHERE course = $course AND chapter_key = $chapter_key;
            """,
            {
                "course": ensure_record_id(course_id),
                "chapter_key": chapter.chapter_key,
                "block_keys": sorted(block_keys),
                "exercise_keys": sorted(exercise_keys),
            },
        )

    async def generate_chapter(
        self,
        *,
        run: CourseGenerationRun,
        course_id: str,
        chapter_key: str,
        anchor_ids: list[str],
        model: ModelSelection,
        prompt_version: str,
    ) -> Chapter:
        course = await Course.get(course_id)
        self.validate_run_request(
            run,
            model=model,
            prompt_version=prompt_version,
            chapter_key=chapter_key,
        )
        version, outline = await self.approved_version(course)
        if run.course_version != str(version.id):
            raise ValueError("Course chapter generation run is stale")
        if version.status == sm.VersionStatus.PUBLISHED:
            raise ValueError("Published Course versions are immutable")
        chapter_no, proposal = self._outline_chapter(outline, chapter_key)
        if set(anchor_ids) != set(proposal.anchor_ids):
            raise ValueError("Chapter evidence must match the approved outline")
        _, _, context = await self.grounded_inputs(course=course, anchor_ids=anchor_ids)
        existing = _rows(
            Chapter,
            await repo_query(
                "SELECT * FROM chapter WHERE course_version = $version AND input_hash = $hash;",
                {"version": ensure_record_id(str(version.id)), "hash": run.input_hash},
            ),
        )
        if existing:
            chapter = existing[0]
        else:
            async with course_job_lock():
                artifact = await self.generation.generate_chapter(
                    course_id=course_id,
                    chapter_key=chapter_key,
                    anchor_ids=anchor_ids,
                    evidence=context,
                    model=model,
                    prompt_version=prompt_version,
                )
            if artifact.chapter_key != chapter_key:
                raise ValueError("Generated chapter key does not match the request")
            self.generation.validate_chapter_composition(
                artifact, approved_lab_keys=set(proposal.lab_keys)
            )
            siblings = await CourseVersion.chapters(str(version.id))
            chapter = Chapter(
                course_version=str(version.id),
                chapter_no=chapter_no,
                title=proposal.title,
                chapter_key=chapter_key,
                version_no=max(
                    (
                        item.version_no
                        for item in siblings
                        if item.chapter_key == chapter_key
                    ),
                    default=0,
                )
                + 1,
                artifact=artifact.model_dump(mode="json"),
                input_hash=run.input_hash,
                citations=[{"anchor_id": item} for item in artifact.citations],
            )
            await chapter.save()
            await chapter.transition_to(sm.ChapterStatus.GENERATING)
            await chapter.transition_to(sm.ChapterStatus.REVIEWING)

        await self._ensure_labs(version, chapter)
        artifact = ChapterArtifact.model_validate(chapter.artifact)
        await self._refresh_stable_links(
            course_id=course_id, chapter=chapter, artifact=artifact
        )
        if version.status == sm.VersionStatus.DRAFT:
            await version.transition_to(sm.VersionStatus.GENERATING)
        if course.status == sm.CourseStatus.OUTLINE_APPROVED:
            await course.transition_to(sm.CourseStatus.GENERATING)
        await self.complete_run(run, chapter.artifact or {})
        return chapter

    @staticmethod
    async def _persist_findings(
        *,
        run: CourseGenerationRun,
        course_id: str,
        version_id: str,
        chapter: Chapter,
        findings: list[ValidationFinding],
    ) -> None:
        for finding in findings:
            payload = finding.model_copy(
                update={"reviewer_run_id": str(run.id)}
            ).model_dump(mode="json")
            identity = hashlib.sha256(
                json.dumps(
                    [str(run.id), payload],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:48]
            await repo_query(
                """
                UPSERT $finding_id CONTENT {
                    course: $course,
                    course_version: $version,
                    chapter: $chapter,
                    generation_run: $run,
                    chapter_key: $chapter_key,
                    finding: $finding,
                    severity: $severity,
                    status: $status,
                    resolution_reason: $resolution_reason
                };
                """,
                {
                    "finding_id": ensure_record_id(
                        f"course_validation_finding:{identity}"
                    ),
                    "course": ensure_record_id(course_id),
                    "version": ensure_record_id(version_id),
                    "chapter": ensure_record_id(str(chapter.id)),
                    "run": ensure_record_id(str(run.id)),
                    "chapter_key": chapter.chapter_key,
                    "finding": payload,
                    "severity": finding.severity,
                    "status": finding.status,
                    "resolution_reason": finding.resolution_reason,
                },
            )

    async def review_chapter(
        self,
        *,
        run: CourseGenerationRun,
        course_id: str,
        chapter_key: str,
        anchor_ids: list[str],
        model: ModelSelection,
        prompt_version: str,
    ) -> tuple[Chapter, list[ValidationFinding]]:
        course = await Course.get(course_id)
        self.validate_run_request(
            run,
            model=model,
            prompt_version=prompt_version,
            chapter_key=chapter_key,
        )
        version, outline = await self.approved_version(course)
        if run.course_version != str(version.id):
            raise ValueError("Course chapter review run is stale")
        _, proposal = self._outline_chapter(outline, chapter_key)
        if set(anchor_ids) != set(proposal.anchor_ids):
            raise ValueError("Review evidence must match the approved outline")
        await self.grounded_inputs(course=course, anchor_ids=anchor_ids)
        chapters = await CourseVersion.chapters(str(version.id))
        matches = [item for item in chapters if item.chapter_key == chapter_key]
        if not matches:
            raise NotFoundError("Chapter not found")
        chapter = max(matches, key=lambda item: item.version_no)
        if run.chapter != str(chapter.id):
            raise ValueError("Course chapter review run is stale")
        if chapter.status == sm.ChapterStatus.PUBLISHED:
            raise ValueError("Published chapters are immutable")
        artifact = ChapterArtifact.model_validate(chapter.artifact)

        existing_rows = await repo_query(
            "SELECT * FROM course_validation_finding WHERE generation_run = $run;",
            {"run": ensure_record_id(str(run.id))},
        )
        if run.output_hash is not None:
            findings = [
                ValidationFinding.model_validate(row["finding"])
                for row in existing_rows
            ]
        else:
            async with course_job_lock():
                reviewed = await self.generation.review(
                    course_id=course_id,
                    chapter_key=chapter_key,
                    anchor_ids=anchor_ids,
                    artifact=artifact,
                    model=model,
                    prompt_version=prompt_version,
                )
            findings = reviewed.findings + self.generation.validate_chapter(
                artifact, set(anchor_ids)
            )
            unique: dict[str, ValidationFinding] = {}
            for finding in findings:
                identity = json.dumps(
                    finding.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                unique.setdefault(identity, finding)
            findings = list(unique.values())
            # A worker crash can leave a prefix of the finding set persisted.
            # Clear that incomplete prefix before deterministic upserts replay it.
            await repo_query(
                "DELETE course_validation_finding WHERE generation_run = $run;",
                {"run": ensure_record_id(str(run.id))},
            )
            await self._persist_findings(
                run=run,
                course_id=course_id,
                version_id=str(version.id),
                chapter=chapter,
                findings=findings,
            )

        try:
            self.generation.assert_publishable(findings)
        except PublicationBlocked:
            if chapter.review_status == sm.ChapterReviewStatus.PENDING:
                await chapter.transition_review(sm.ChapterReviewStatus.ESCALATED)
            if chapter.status == sm.ChapterStatus.REVIEWING:
                await chapter.transition_to(sm.ChapterStatus.BLOCKED)
        else:
            if chapter.review_status == sm.ChapterReviewStatus.PENDING:
                await chapter.transition_review(sm.ChapterReviewStatus.PASSED)
            if chapter.validation_status == sm.ChapterValidationStatus.PENDING:
                await chapter.transition_validation(sm.ChapterValidationStatus.PASSED)
            if chapter.status == sm.ChapterStatus.REVIEWING:
                await chapter.transition_to(sm.ChapterStatus.READY)
        await self.complete_run(
            run, [finding.model_dump(mode="json") for finding in findings]
        )
        return chapter, findings


__all__ = ["CourseWorkflowService"]
