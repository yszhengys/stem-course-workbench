"""Persistent Course command claims and recoverable queue binding."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from surreal_commands import submit_command

from open_notebook.course import state_machine as sm
from open_notebook.course.contracts import ModelSelection, ValidationFinding
from open_notebook.course.evidence_service import EvidenceInputError, EvidenceService
from open_notebook.course.generation_service import (
    CourseGenerationService,
    PublicationBlocked,
)
from open_notebook.course.models import (
    Chapter,
    Course,
    CourseEvidenceAnchor,
    CourseGenerationRun,
    CourseValidationFinding,
    CourseVersion,
)
from open_notebook.course.workflow_service import (
    CourseWorkflowService,
    generation_input_hash,
)
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import Notebook, Source
from open_notebook.exceptions import NotFoundError

ACTIVE_RUN_STATUSES = {"queued", "running"}
FRAMEWORK_TO_RUN_STATUS = {
    "running": "running",
    "completed": "succeeded",
    "failed": "failed",
    "canceled": "cancelled",
    "cancelled": "cancelled",
}
ALLOWED_RUN_TRANSITIONS = {
    "queued": {"running", "succeeded", "failed", "cancelled"},
    "running": {"succeeded", "failed", "cancelled"},
}

_claim_locks: dict[str, asyncio.Lock] = {}
_claim_locks_guard = asyncio.Lock()


@dataclass(frozen=True)
class CourseJobSubmission:
    command_id: str
    run_id: str
    status: str


async def _lock_for(input_hash: str) -> asyncio.Lock:
    async with _claim_locks_guard:
        return _claim_locks.setdefault(input_hash, asyncio.Lock())


def next_course_run_status(
    current_status: str, framework_status: str
) -> str | None:
    """Map one framework observation to an allowed monotonic Course transition."""

    candidate = FRAMEWORK_TO_RUN_STATUS.get(framework_status)
    if candidate in ALLOWED_RUN_TRANSITIONS.get(current_status, set()):
        return candidate
    return None


class CourseCommandService:
    """Create persistent run claims before submitting surreal-commands jobs."""

    @staticmethod
    async def _framework_status(command_id: str) -> tuple[str | None, str | None]:
        rows = await repo_query(
            "SELECT status, error_message FROM $command_id",
            {"command_id": ensure_record_id(command_id)},
        )
        if not rows:
            return None, None
        return str(rows[0].get("status")), rows[0].get("error_message")

    @staticmethod
    async def _set_run_status(
        run_id: str,
        *,
        expected_status: str,
        status: str,
        error_message: str | None = None,
    ) -> dict[str, Any] | None:
        rows = await repo_query(
            """
            UPDATE $run_id SET status = $status, error_message = $error_message
            WHERE status = $expected_status
            RETURN AFTER;
            """,
            {
                "run_id": ensure_record_id(run_id),
                "expected_status": expected_status,
                "status": status,
                "error_message": error_message,
            },
        )
        return rows[0] if rows else None

    async def _sync_active_row(self, row: dict[str, Any]) -> dict[str, Any]:
        command_id = row.get("command")
        if not command_id:
            return row
        framework_status, error_message = await self._framework_status(str(command_id))
        current_status = str(row.get("status") or "")
        mapped = next_course_run_status(current_status, framework_status or "")
        if mapped:
            updated = await self._set_run_status(
                str(row["id"]),
                expected_status=current_status,
                status=mapped,
                error_message=error_message,
            )
            if updated is not None:
                return updated
            # A concurrent worker may have advanced the run after this row was
            # read. Reload instead of returning stale active state.
            rows = await repo_query(
                "SELECT * FROM $run_id;",
                {"run_id": ensure_record_id(str(row["id"]))},
            )
            if rows:
                return rows[0]
        return row

    @staticmethod
    async def _find_unbound_command(
        run_id: str,
        *,
        command_name: str,
        queue_args: dict[str, Any],
    ) -> str | None:
        rows = await repo_query(
            "SELECT * FROM command WHERE app = 'open_notebook' "
            "AND args.run_id = $run_id ORDER BY created DESC LIMIT 1;",
            {"run_id": run_id},
        )
        if not rows:
            return None
        candidate = rows[0]
        if (
            candidate.get("name") != command_name
            or candidate.get("args") != queue_args
            or not candidate.get("id")
        ):
            return None
        return str(candidate["id"])

    @staticmethod
    async def _bind_command(run_id: str, command_id: str) -> None:
        rows = await repo_query(
            """
            UPDATE $run_id SET command = $command_id
            WHERE (command = NONE OR command = $command_id)
                AND status IN ['queued', 'running']
            RETURN AFTER;
            """,
            {
                "run_id": ensure_record_id(run_id),
                "command_id": ensure_record_id(command_id),
            },
        )
        if rows:
            return

        current_rows = await repo_query(
            "SELECT * FROM $run_id;",
            {"run_id": ensure_record_id(run_id)},
        )
        if current_rows:
            status = str(current_rows[0].get("status") or "unknown")
            if status not in ACTIVE_RUN_STATUSES:
                raise ValueError(f"Course generation run is {status}")
        raise ValueError("Course generation run was claimed by another command")

    async def _ensure_bound(
        self,
        *,
        row: dict[str, Any],
        command_name: str,
        command_args: dict[str, Any],
    ) -> CourseJobSubmission:
        run_id = str(row["id"])
        command_id = row.get("command")
        queue_args = {**command_args, "run_id": run_id}
        if not command_id:
            command_id = await self._find_unbound_command(
                run_id,
                command_name=command_name,
                queue_args=queue_args,
            )
        if not command_id:
            command_id = await asyncio.to_thread(
                submit_command,
                "open_notebook",
                command_name,
                queue_args,
            )
        command_id = str(command_id)
        if row.get("command") != command_id:
            await self._bind_command(run_id, command_id)
        return CourseJobSubmission(
            command_id=command_id,
            run_id=run_id,
            status=str(row.get("status") or "queued"),
        )

    async def submit_stage(
        self,
        *,
        course_id: str,
        stage: str,
        command_name: str,
        command_args: dict[str, Any],
        model: ModelSelection | dict[str, Any],
        prompt_version: str,
        anchor_ids: list[str],
        source_hashes: dict[str, str],
        course_version_id: str | None = None,
        chapter_id: str | None = None,
        chapter_key: str | None = None,
        force: bool = False,
    ) -> CourseJobSubmission:
        selection = ModelSelection.model_validate(model)
        input_hash = generation_input_hash(
            course_id=course_id,
            stage=stage,
            command_args=command_args,
            model=selection,
            prompt_version=prompt_version,
            anchor_ids=anchor_ids,
            source_hashes=source_hashes,
            course_version_id=course_version_id,
            chapter_id=chapter_id,
            chapter_key=chapter_key,
        )
        claim_lock = await _lock_for(input_hash)
        async with claim_lock:
            rows = await repo_query(
                """
                SELECT * FROM course_generation_run WHERE input_hash = $input_hash
                ORDER BY created DESC;
                """,
                {"input_hash": input_hash},
            )
            if not force:
                for candidate in rows:
                    if candidate.get("status") not in ACTIVE_RUN_STATUSES:
                        continue
                    candidate = await self._sync_active_row(candidate)
                    if candidate.get("status") in ACTIVE_RUN_STATUSES:
                        return await self._ensure_bound(
                            row=candidate,
                            command_name=command_name,
                            command_args=command_args,
                        )

            attempt = len(rows) + 1
            run_id = f"course_generation_run:{input_hash[:48]}_{attempt}"
            payload = {
                "course": ensure_record_id(course_id),
                "course_version": (
                    ensure_record_id(course_version_id) if course_version_id else None
                ),
                "chapter": ensure_record_id(chapter_id) if chapter_id else None,
                "chapter_key": chapter_key,
                "stage": stage,
                "adapter": selection.adapter,
                "model": selection.model,
                "reasoning_effort": selection.reasoning_effort,
                "status": "queued",
                "prompt_version": prompt_version,
                "input_hash": input_hash,
                "output_hash": None,
                "command": None,
                "error_message": None,
            }
            created = await repo_query(
                "CREATE ONLY $run_id CONTENT $payload RETURN AFTER;",
                {
                    "run_id": ensure_record_id(run_id),
                    "payload": payload,
                },
            )
            row = created[0] if created else {"id": run_id, **payload}
            return await self._ensure_bound(
                row=row,
                command_name=command_name,
                command_args=command_args,
            )

    @staticmethod
    async def _course(course_id: str) -> Course:
        return await Course.get(course_id)

    @staticmethod
    async def eligible_sources(course_id: str) -> list[dict[str, Any]]:
        course = await Course.get(course_id)
        notebook = await Notebook.get(course.notebook)
        sources = await notebook.get_sources()
        result: list[dict[str, Any]] = []
        for source in sources:
            file_path = source.asset.file_path if source.asset else None
            if not file_path or not str(file_path).lower().endswith((".pdf", ".pptx")):
                continue
            source_id = str(source.id)
            role = (
                "PRIMARY"
                if source_id in course.primary_source_ids
                else "SUPPLEMENT"
                if source_id in course.supplement_source_ids
                else None
            )
            result.append(
                {
                    "source_id": source_id,
                    "title": source.title,
                    "filename": Path(str(file_path)).name,
                    "kind": "pdf" if str(file_path).lower().endswith(".pdf") else "pptx",
                    "role": role,
                    "associated": role is not None,
                }
            )
        return result

    @staticmethod
    async def list_anchors(course_id: str) -> list[CourseEvidenceAnchor]:
        await Course.get(course_id)
        rows = await repo_query(
            """
            SELECT * FROM course_evidence_anchor
            WHERE course = $course AND is_current = true
            ORDER BY source, locator.index, locator.block_key;
            """,
            {"course": ensure_record_id(course_id)},
        )
        return [CourseEvidenceAnchor(**row) for row in rows]

    async def submit_evidence(
        self,
        *,
        course_id: str,
        source_id: str,
        role: str,
        force: bool = False,
    ) -> CourseJobSubmission:
        course = await Course.get(course_id)
        expected = (
            "PRIMARY"
            if source_id in course.primary_source_ids
            else "SUPPLEMENT"
            if source_id in course.supplement_source_ids
            else None
        )
        if role not in {"PRIMARY", "SUPPLEMENT"} or role != expected:
            raise EvidenceInputError("Source role does not match this Course.")
        source = await Source.get(source_id)
        path = source.asset.file_path if source.asset else None
        if not path:
            raise EvidenceInputError("Course Source has no local PDF or PPTX asset.")
        evidence = EvidenceService()
        safe_path = evidence.resolve_safe_source_path(path)
        evidence.validate_extension(safe_path)
        source_hash = evidence.sha256_file(safe_path)
        return await self.submit_stage(
            course_id=course_id,
            stage="evidence",
            command_name="course_build_evidence",
            command_args={
                "course_id": course_id,
                "source_id": source_id,
                "role": role,
            },
            model=ModelSelection(adapter="open_notebook", model="docling"),
            prompt_version="evidence-v1",
            anchor_ids=[],
            source_hashes={source_id: source_hash},
            force=force,
        )

    @staticmethod
    async def _grounded(
        course_id: str, anchor_ids: list[str]
    ) -> tuple[Course, dict[str, str], list[str]]:
        course = await Course.get(course_id)
        _, source_hashes, context = await CourseWorkflowService().grounded_inputs(
            course=course, anchor_ids=anchor_ids
        )
        return course, source_hashes, context

    async def submit_outline(
        self,
        *,
        course_id: str,
        anchor_ids: list[str],
        available_lab_keys: list[str],
        prompt_version: str,
        model: ModelSelection,
        force: bool = False,
    ) -> CourseJobSubmission:
        course, source_hashes, _ = await self._grounded(course_id, anchor_ids)
        arguments = {
            "course_id": course_id,
            "anchor_ids": anchor_ids,
            "available_lab_keys": available_lab_keys,
            "prompt_version": prompt_version,
            "model": model.model_dump(mode="json"),
        }
        return await self.submit_stage(
            course_id=course_id,
            course_version_id=course.outline_version_id,
            stage="outline",
            command_name="course_generate_outline",
            command_args=arguments,
            model=model,
            prompt_version=prompt_version,
            anchor_ids=anchor_ids,
            source_hashes=source_hashes,
            force=force,
        )

    async def submit_chapter(
        self,
        *,
        course_id: str,
        chapter_key: str,
        anchor_ids: list[str],
        prompt_version: str,
        model: ModelSelection,
        force: bool = False,
    ) -> CourseJobSubmission:
        course, source_hashes, _ = await self._grounded(course_id, anchor_ids)
        version, outline = await CourseWorkflowService.approved_version(course)
        CourseWorkflowService._outline_chapter(outline, chapter_key)
        arguments = {
            "course_id": course_id,
            "chapter_key": chapter_key,
            "anchor_ids": anchor_ids,
            "prompt_version": prompt_version,
            "model": model.model_dump(mode="json"),
        }
        return await self.submit_stage(
            course_id=course_id,
            course_version_id=str(version.id),
            stage="chapter_content",
            command_name="course_generate_chapter",
            command_args=arguments,
            model=model,
            prompt_version=prompt_version,
            anchor_ids=anchor_ids,
            source_hashes=source_hashes,
            chapter_key=chapter_key,
            force=force,
        )

    async def submit_review(
        self,
        *,
        course_id: str,
        chapter_key: str,
        anchor_ids: list[str],
        prompt_version: str,
        model: ModelSelection,
        force: bool = False,
    ) -> CourseJobSubmission:
        course, source_hashes, _ = await self._grounded(course_id, anchor_ids)
        version, outline = await CourseWorkflowService.approved_version(course)
        CourseWorkflowService._outline_chapter(outline, chapter_key)
        chapters = await CourseVersion.chapters(str(version.id))
        matches = [item for item in chapters if item.chapter_key == chapter_key]
        if not matches:
            raise NotFoundError("Chapter not found")
        chapter = max(matches, key=lambda item: item.version_no)
        arguments = {
            "course_id": course_id,
            "chapter_key": chapter_key,
            "anchor_ids": anchor_ids,
            "prompt_version": prompt_version,
            "model": model.model_dump(mode="json"),
        }
        return await self.submit_stage(
            course_id=course_id,
            course_version_id=str(version.id),
            chapter_id=str(chapter.id),
            stage="review",
            command_name="course_review_chapter",
            command_args=arguments,
            model=model,
            prompt_version=prompt_version,
            anchor_ids=anchor_ids,
            source_hashes=source_hashes,
            chapter_key=chapter_key,
            force=force,
        )

    async def get_run(
        self, course_id: str, run_id: str
    ) -> CourseGenerationRun:
        run = await CourseGenerationRun.get(run_id)
        if run.course != course_id:
            raise NotFoundError("Course generation run not found")
        if run.status in ACTIVE_RUN_STATUSES and run.command:
            row = await self._sync_active_row(run.model_dump(mode="json"))
            run = CourseGenerationRun(**row)
        return run

    @staticmethod
    async def current_outline(course_id: str) -> CourseVersion:
        course = await Course.get(course_id)
        if not course.outline_version_id:
            raise NotFoundError("Course outline not found")
        version = await CourseVersion.get(course.outline_version_id)
        if version.course != course_id:
            raise NotFoundError("Course outline not found")
        return version

    @staticmethod
    async def current_chapter(course_id: str, chapter_key: str) -> Chapter:
        course = await Course.get(course_id)
        try:
            version, outline = await CourseWorkflowService.approved_version(course)
            CourseWorkflowService._outline_chapter(outline, chapter_key)
        except (NotFoundError, ValueError):
            raise NotFoundError("Chapter not found")
        chapters = await CourseVersion.chapters(str(version.id))
        matches = [item for item in chapters if item.chapter_key == chapter_key]
        if not matches:
            raise NotFoundError("Chapter not found")
        return max(matches, key=lambda item: item.version_no)

    @staticmethod
    async def list_findings(
        course_id: str, chapter_key: str | None = None
    ) -> list[CourseValidationFinding]:
        await Course.get(course_id)
        rows = await repo_query(
            """
            SELECT * FROM course_validation_finding
            WHERE course = $course AND ($chapter_key = NONE OR chapter_key = $chapter_key)
            ORDER BY created;
            """,
            {"course": ensure_record_id(course_id), "chapter_key": chapter_key},
        )
        return [CourseValidationFinding(**row) for row in rows]

    @staticmethod
    async def update_finding(
        *,
        course_id: str,
        finding_id: str,
        status: str,
        resolution_reason: str,
    ) -> CourseValidationFinding:
        finding = await CourseValidationFinding.get(finding_id)
        if finding.course != course_id:
            raise NotFoundError("Validation finding not found")
        artifact = ValidationFinding.model_validate(finding.finding).model_copy(
            update={"status": status, "resolution_reason": resolution_reason}
        )
        # Revalidate conditional publication semantics at the boundary.
        artifact = ValidationFinding.model_validate(artifact.model_dump(mode="json"))
        finding.finding = artifact.model_dump(mode="json")
        finding.status = artifact.status
        finding.resolution_reason = artifact.resolution_reason
        await finding.save()
        if finding.chapter:
            rows = await repo_query(
                "SELECT * FROM course_validation_finding WHERE chapter = $chapter;",
                {"chapter": ensure_record_id(finding.chapter)},
            )
            artifacts = [
                ValidationFinding.model_validate(row["finding"]) for row in rows
            ]
            try:
                CourseGenerationService.assert_publishable(artifacts)
            except PublicationBlocked:
                pass
            else:
                chapter = await Chapter.get(finding.chapter)
                if chapter.review_status == sm.ChapterReviewStatus.ESCALATED:
                    await chapter.transition_review(sm.ChapterReviewStatus.PASSED)
                if chapter.validation_status == sm.ChapterValidationStatus.PENDING:
                    await chapter.transition_validation(
                        sm.ChapterValidationStatus.PASSED
                    )
                if chapter.status == sm.ChapterStatus.BLOCKED:
                    await chapter.transition_to(sm.ChapterStatus.GENERATING)
                    await chapter.transition_to(sm.ChapterStatus.REVIEWING)
                    await chapter.transition_to(sm.ChapterStatus.READY)
        return finding

    @staticmethod
    async def retrieval_context(
        course_id: str, anchor_ids: list[str]
    ) -> dict[str, Any]:
        _, _, context = await CourseCommandService._grounded(course_id, anchor_ids)
        return {
            "course_id": course_id,
            "anchor_ids": anchor_ids,
            "context": context,
        }


__all__ = ["CourseCommandService", "CourseJobSubmission"]
