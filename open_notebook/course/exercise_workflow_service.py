"""Replay-safe, atomic workflow for one reviewed chapter exercise bank."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable
from typing import Any

from open_notebook.database.repository import ensure_record_id, repo_query

from . import state_machine as sm
from .assessment_service import AssessmentService
from .contracts import ModelSelection, ValidationFinding
from .models import (
    Chapter,
    Course,
    CourseEvidenceAnchor,
    CourseGenerationRun,
    CourseVersion,
)
from .v2_contracts import (
    ExerciseBlueprint,
    ExerciseVerification,
    TransferTaskSpec,
)
from .v2_models import CourseExercise
from .workflow_service import (
    CourseWorkflowService,
    _artifact_hash,
    _canonical_json,
    _rows,
    generation_input_hash,
)

_exercise_locks: dict[str, asyncio.Lock] = {}
_exercise_locks_guard = asyncio.Lock()


async def _exercise_lock_for(parent_run_id: str) -> asyncio.Lock:
    async with _exercise_locks_guard:
        return _exercise_locks.setdefault(parent_run_id, asyncio.Lock())


def exercise_record_id(
    version_id: str, chapter_key: str, exercise_key: str
) -> str:
    digest = hashlib.sha256(
        f"{version_id}\0{chapter_key}\0{exercise_key}".encode()
    ).hexdigest()[:48]
    return f"course_exercise:{digest}"


def exercise_generation_claim_args(
    *,
    course_id: str,
    version_id: str,
    chapter_key: str,
    anchor_ids: list[str],
    generation_model: ModelSelection,
    review_model: ModelSelection,
    prompt_version: str,
) -> dict[str, Any]:
    return {
        "course_id": course_id,
        "course_version_id": version_id,
        "chapter_key": chapter_key,
        "anchor_ids": anchor_ids,
        "prompt_version": prompt_version,
        "model": generation_model.model_dump(mode="json"),
        "review_model": review_model.model_dump(mode="json"),
    }


def canonical_exercise_output(
    exercises: Iterable[CourseExercise],
) -> list[dict[str, Any]]:
    fields = {
        "id",
        "course",
        "course_version",
        "chapter",
        "chapter_key",
        "exercise_key",
        "blueprint",
        "source_anchor_ids",
        "difficulty",
        "grader",
        "is_core",
        "is_gating",
        "is_source_level",
        "verification",
        "generation_run",
        "review_run_ids",
    }
    payloads = [
        exercise.model_dump(mode="json", include=fields) for exercise in exercises
    ]
    return sorted(payloads, key=lambda item: (item["exercise_key"], item["id"]))


def _canonical_exercise_review_output(
    run: CourseGenerationRun, findings: Iterable[ValidationFinding]
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for finding in findings:
        payload = finding.model_dump(mode="json", exclude={"reviewer_run_id"})
        payload["reviewer_run_id"] = str(run.id)
        payloads.append(payload)
    return sorted(payloads, key=_canonical_json)


class ExerciseWorkflowService:
    """Generate, review, and replace a chapter bank as one durable operation."""

    def __init__(self, *, workflow: CourseWorkflowService | None = None) -> None:
        self.workflow = workflow or CourseWorkflowService()

    @staticmethod
    async def load_course(course_id: str) -> Course:
        return await Course.get(course_id)

    async def _load_exercises_for_run(
        self,
        *,
        run: CourseGenerationRun,
        version_id: str,
        chapter: Chapter,
    ) -> tuple[CourseExercise, ...]:
        rows = await repo_query(
            "SELECT * FROM course_exercise WHERE generation_run = $run "
            "ORDER BY exercise_key;",
            {"run": ensure_record_id(str(run.id))},
        )
        exercises = tuple(_rows(CourseExercise, rows))
        if not exercises or any(
            exercise.course != run.course
            or exercise.course_version != version_id
            or exercise.chapter != str(chapter.id)
            or exercise.chapter_key != chapter.chapter_key
            for exercise in exercises
        ):
            raise ValueError("Persisted exercise bank does not match its run")
        self.workflow.verify_completed_output(
            run, canonical_exercise_output(exercises)
        )
        return exercises

    async def review_transfer(
        self,
        *,
        parent_run: CourseGenerationRun,
        course_id: str,
        version_id: str,
        chapter: Chapter,
        core: ExerciseBlueprint,
        selected_anchors: list[CourseEvidenceAnchor],
        source_hashes: dict[str, str],
        model: ModelSelection | None,
        prompt_version: str,
    ) -> tuple[tuple[ValidationFinding, ...], str]:
        if model is None:
            raise ValueError("An explicit exercise review model is required")
        transfer = core.transfer_task
        if transfer is None or not core.is_core:
            raise ValueError("Exercise review requires one core transfer")
        required_anchor_ids = list(
            dict.fromkeys((*core.source_anchor_ids, *transfer.anchor_ids))
        )
        by_anchor = {anchor.anchor_id: anchor for anchor in selected_anchors}
        if not required_anchor_ids or any(
            anchor_id not in by_anchor for anchor_id in required_anchor_ids
        ):
            raise ValueError("Exercise review evidence is outside the parent scope")
        required_source_ids = {
            by_anchor[anchor_id].source for anchor_id in required_anchor_ids
        }
        if not required_source_ids.issubset(source_hashes):
            raise ValueError("Exercise review source hashes are incomplete")
        required_source_hashes = {
            source_id: source_hashes[source_id]
            for source_id in sorted(required_source_ids)
        }
        core_payload = core.model_dump(
            mode="json", exclude={"transfer_task"}, exclude_none=True
        )
        transfer_payload = transfer.model_dump(mode="json", exclude_none=True)
        child_args = {
            "parent_run_id": str(parent_run.id),
            "course_id": course_id,
            "chapter_key": chapter.chapter_key,
            "prompt_version": prompt_version,
            "model": model.model_dump(mode="json"),
            "core": core_payload,
            "transfer": transfer_payload,
            "anchor_ids": required_anchor_ids,
        }
        input_hash = generation_input_hash(
            course_id=course_id,
            stage="exercise_bank_review",
            command_args=child_args,
            model=model,
            prompt_version=prompt_version,
            anchor_ids=required_anchor_ids,
            source_hashes=required_source_hashes,
            course_version_id=version_id,
            chapter_id=str(chapter.id),
            chapter_key=chapter.chapter_key,
        )
        rows = await repo_query(
            "SELECT * FROM course_generation_run WHERE input_hash = $input_hash "
            "ORDER BY created DESC;",
            {"input_hash": input_hash},
        )
        children = _rows(CourseGenerationRun, rows)
        if len(children) > 1:
            raise ValueError("Exercise review run is not unique")
        child = children[0] if children else None
        if child is None:
            child_id = f"course_generation_run:{input_hash[:48]}_1"
            payload = {
                "course": ensure_record_id(course_id),
                "course_version": ensure_record_id(version_id),
                "chapter": ensure_record_id(str(chapter.id)),
                "chapter_key": chapter.chapter_key,
                "stage": "exercise_bank_review",
                "adapter": model.adapter,
                "model": model.model,
                "reasoning_effort": model.reasoning_effort,
                "status": sm.RunStatus.QUEUED,
                "prompt_version": prompt_version,
                "input_hash": input_hash,
                "output_hash": None,
                "command": None,
                "error_message": None,
            }
            try:
                created = await repo_query(
                    "CREATE ONLY $run_id CONTENT $payload RETURN AFTER;",
                    {
                        "run_id": ensure_record_id(child_id),
                        "payload": payload,
                    },
                )
                child = _rows(CourseGenerationRun, created)[0]
            except (RuntimeError, IndexError):
                child = await CourseGenerationRun.get(child_id)
        if not isinstance(child, CourseGenerationRun):
            raise ValueError("Exercise review run could not be created")
        if (
            child.course != course_id
            or child.course_version != version_id
            or child.chapter != str(chapter.id)
            or child.chapter_key != chapter.chapter_key
            or child.stage != "exercise_bank_review"
            or child.adapter != model.adapter
            or child.model != model.model
            or child.reasoning_effort != model.reasoning_effort
            or child.prompt_version != prompt_version
            or child.input_hash != input_hash
            or child.command is not None
        ):
            raise ValueError("Exercise review run ownership mismatch")

        if child.status == sm.RunStatus.SUCCEEDED:
            findings = tuple(await self.workflow._finding_rows(child))
            self.workflow.verify_completed_output(
                child, _canonical_exercise_review_output(child, findings)
            )
            return findings, str(child.id)
        if child.status in {sm.RunStatus.FAILED, sm.RunStatus.CANCELLED}:
            raise ValueError("Exercise review run is terminal")
        if child.status == sm.RunStatus.QUEUED:
            claimed = _rows(
                CourseGenerationRun,
                await repo_query(
                    "UPDATE $run SET status = 'running' "
                    "WHERE status = 'queued' AND command = NONE RETURN AFTER;",
                    {"run": ensure_record_id(str(child.id))},
                ),
            )
            child = (
                claimed[0]
                if claimed
                else await CourseGenerationRun.get(str(child.id))
            )
        if child.status != sm.RunStatus.RUNNING:
            raise ValueError("Exercise review run is no longer active")

        try:
            checkpointed = tuple(await self.workflow._finding_rows(child))
            if checkpointed:
                await self.workflow._persist_findings(
                    run=child,
                    course_id=course_id,
                    version_id=version_id,
                    chapter=chapter,
                    findings=list(checkpointed),
                    completion_output=_canonical_exercise_review_output(
                        child, checkpointed
                    ),
                )
                return checkpointed, str(child.id)
            evidence_by_anchor = {
                anchor_id: by_anchor[anchor_id].locator.quote
                for anchor_id in required_anchor_ids
            }
            findings = await self.workflow.generation.review_exercise_transfer(
                course_id=course_id,
                chapter_key=chapter.chapter_key,
                core=core,
                evidence_by_anchor=evidence_by_anchor,
                model=model,
                prompt_version=prompt_version,
            )
            await self.workflow._persist_findings(
                run=child,
                course_id=course_id,
                version_id=version_id,
                chapter=chapter,
                findings=list(findings),
                completion_output=_canonical_exercise_review_output(child, findings),
            )
            return findings, str(child.id)
        except Exception as exc:
            await self.workflow.fail_run(child, str(exc))
            raise

    @staticmethod
    def _exercise_record(
        *,
        course_id: str,
        version_id: str,
        chapter: Chapter,
        blueprint: ExerciseBlueprint,
        parent_run_id: str,
        review_run_ids: tuple[str, ...],
    ) -> CourseExercise:
        verification = ExerciseVerification(
            level="L1",
            method=(
                "independent_model_review"
                if blueprint.is_core and review_run_ids
                else "self_consistency"
            ),
        )
        return CourseExercise(
            id=exercise_record_id(version_id, chapter.chapter_key, blueprint.key),
            course=course_id,
            course_version=version_id,
            chapter=str(chapter.id),
            chapter_key=chapter.chapter_key,
            exercise_key=blueprint.key,
            blueprint=blueprint,
            source_anchor_ids=blueprint.source_anchor_ids,
            difficulty=blueprint.difficulty,
            grader=blueprint.grader,
            is_core=blueprint.is_core,
            is_gating=blueprint.is_gating,
            is_source_level=blueprint.is_source_level,
            verification=verification,
            generation_run=parent_run_id,
            review_run_ids=review_run_ids,
        )

    @staticmethod
    def _persistence_record(exercise: CourseExercise) -> dict[str, Any]:
        content = exercise._prepare_save_data()
        for field_name in ("id", "created", "updated"):
            content.pop(field_name, None)
        return {
            "id": ensure_record_id(str(exercise.id)),
            "content": content,
        }

    async def _persist_atomically(
        self,
        *,
        run: CourseGenerationRun,
        course: Course,
        version: CourseVersion,
        chapter: Chapter,
        anchors: list[CourseEvidenceAnchor],
        exercises: tuple[CourseExercise, ...],
    ) -> None:
        output = canonical_exercise_output(exercises)
        output_hash = _artifact_hash({"output": output})
        anchor_snapshots = [
            {
                "anchor_id": anchor.anchor_id,
                "source": ensure_record_id(anchor.source),
                "quote_sha256": anchor.quote_sha256,
                "content_sha256": anchor.locator.content_sha256,
            }
            for anchor in anchors
        ]
        try:
            await repo_query(
                """
            BEGIN TRANSACTION;
            LET $current_course = (
                SELECT VALUE id FROM course
                WHERE id = $course AND outline_version_id = $version
            );
            IF array::len($current_course) != 1 {
                THROW 'Course exercise scope changed'
            };
            LET $mutable_version = (
                SELECT VALUE id FROM course_version
                WHERE id = $version AND course = $course
                  AND status != 'published' AND outline_hash = $outline_hash
                  AND time::micros(updated) = time::micros($version_updated)
            );
            IF array::len($mutable_version) != 1 {
                THROW 'Course exercise version changed'
            };
            LET $mutable_chapter = (
                SELECT VALUE id FROM chapter
                WHERE id = $chapter AND course_version = $version
                  AND chapter_key = $chapter_key AND status != 'published'
                  AND input_hash = $chapter_input_hash
                  AND time::micros(updated) = time::micros($chapter_updated)
            );
            IF array::len($mutable_chapter) != 1 {
                THROW 'Course exercise chapter changed'
            };
            FOR $anchor IN $anchors {
                LET $matched_anchor = (
                    SELECT VALUE id FROM course_evidence_anchor
                    WHERE course = $course AND source = $anchor.source
                      AND anchor_id = $anchor.anchor_id
                      AND quote_sha256 = $anchor.quote_sha256
                      AND locator.content_sha256 = $anchor.content_sha256
                      AND is_current = true
                );
                IF array::len($matched_anchor) != 1 {
                    THROW 'Course exercise evidence changed'
                };
            };
            LET $active_run = (
                SELECT VALUE id FROM course_generation_run
                WHERE id = $run AND course = $course
                  AND course_version = $version AND chapter = $chapter
                  AND chapter_key = $chapter_key AND stage = 'exercise_bank'
                  AND status = 'running'
            );
            IF array::len($active_run) != 1 {
                THROW 'Course exercise run is no longer active'
            };
            DELETE course_exercise
            WHERE course_version = $version AND chapter_key = $chapter_key;
            FOR $record IN $records {
                CREATE ONLY $record.id CONTENT $record.content;
            };
            LET $completed = (
                UPDATE $run SET status = 'succeeded', output_hash = $output_hash,
                    error_message = NONE
                WHERE status = 'running' RETURN VALUE id
            );
            IF array::len($completed) != 1 {
                THROW 'Course exercise run completion conflict'
            };
            COMMIT TRANSACTION;
            """,
                {
                    "run": ensure_record_id(str(run.id)),
                    "course": ensure_record_id(str(course.id)),
                    "version": ensure_record_id(str(version.id)),
                    "chapter": ensure_record_id(str(chapter.id)),
                    "chapter_key": chapter.chapter_key,
                    "outline_hash": version.outline_hash,
                    "version_updated": version.updated,
                    "chapter_input_hash": chapter.input_hash,
                    "chapter_updated": chapter.updated,
                    "anchors": anchor_snapshots,
                    "records": [
                        self._persistence_record(exercise) for exercise in exercises
                    ],
                    "output_hash": output_hash,
                },
            )
        except RuntimeError:
            current = await CourseGenerationRun.get(str(run.id))
            if current.status != sm.RunStatus.SUCCEEDED:
                raise
            await self._load_exercises_for_run(
                run=current, version_id=str(version.id), chapter=chapter
            )
            run.status = current.status
            run.output_hash = current.output_hash
            run.error_message = current.error_message
            return
        run.status = sm.RunStatus.SUCCEEDED
        run.output_hash = output_hash
        run.error_message = None

    async def generate_and_persist(
        self,
        *,
        run_id: str,
        command_id: str,
        course_id: str,
        version_id: str,
        chapter_key: str,
        anchor_ids: list[str],
        generation_model: ModelSelection,
        review_model: ModelSelection,
        prompt_version: str,
    ) -> tuple[CourseExercise, ...]:
        lock = await _exercise_lock_for(run_id)
        async with lock:
            run = await self.workflow.load_run(
                run_id=run_id,
                course_id=course_id,
                stage="exercise_bank",
                command_id=command_id,
            )
            self.workflow.validate_run_request(
                run,
                model=generation_model,
                prompt_version=prompt_version,
                chapter_key=chapter_key,
            )
            course = await self.load_course(course_id)
            version, _ = await self.workflow.approved_version(course)
            if str(version.id) != version_id:
                raise ValueError("Exercise bank version is not current")
            if version.status == sm.VersionStatus.PUBLISHED:
                raise ValueError("Published Course versions are immutable")
            chapter = await self.workflow.resolve_current_chapter(
                course_id=course_id,
                version_id=version_id,
                chapter_key=chapter_key,
            )
            if run.chapter != str(chapter.id):
                raise ValueError("Exercise bank run targets an old chapter")
            if chapter.status == sm.ChapterStatus.PUBLISHED:
                raise ValueError("Published chapters are immutable")
            selected, source_hashes, _ = await self.workflow.grounded_inputs(
                course=course, anchor_ids=anchor_ids
            )
            command_args = exercise_generation_claim_args(
                course_id=course_id,
                version_id=version_id,
                chapter_key=chapter_key,
                anchor_ids=anchor_ids,
                generation_model=generation_model,
                review_model=review_model,
                prompt_version=prompt_version,
            )
            self.workflow.validate_run_claim(
                run,
                command_args=command_args,
                model=generation_model,
                prompt_version=prompt_version,
                anchor_ids=anchor_ids,
                source_hashes=source_hashes,
            )
            if run.status == sm.RunStatus.SUCCEEDED:
                return await self._load_exercises_for_run(
                    run=run, version_id=version_id, chapter=chapter
                )
            await self.workflow.activate_run(run, command_id)

            review_run_ids: dict[str, str] = {}

            async def reload_anchors(
                requested_course: str,
                requested_version: str,
                requested_anchor_ids: tuple[str, ...],
            ) -> tuple[CourseEvidenceAnchor, ...]:
                if (
                    requested_course != course_id
                    or requested_version != version_id
                    or requested_anchor_ids != tuple(anchor_ids)
                ):
                    raise ValueError("Exercise assessment scope changed")
                current_course = await self.load_course(course_id)
                current, _, _ = await self.workflow.grounded_inputs(
                    course=current_course, anchor_ids=anchor_ids
                )
                return tuple(current)

            async def reload_outline(
                requested_course: str, requested_version: str
            ):
                if (
                    requested_course != course_id
                    or requested_version != version_id
                ):
                    raise ValueError("Exercise assessment scope changed")
                current_course = await self.load_course(course_id)
                current_version, outline = await self.workflow.approved_version(
                    current_course
                )
                if str(current_version.id) != version_id:
                    raise ValueError("Exercise assessment version changed")
                return outline

            async def reload_chapter(
                requested_version: str, requested_chapter_key: str
            ) -> Chapter:
                if (
                    requested_version != version_id
                    or requested_chapter_key != chapter_key
                ):
                    raise ValueError("Exercise assessment scope changed")
                return await self.workflow.resolve_current_chapter(
                    course_id=course_id,
                    version_id=version_id,
                    chapter_key=chapter_key,
                )

            async def review_transfer(
                core: ExerciseBlueprint, transfer: TransferTaskSpec
            ) -> tuple[ValidationFinding, ...]:
                if core.transfer_task != transfer:
                    raise ValueError("Exercise transfer review input changed")
                findings, child_id = await self.review_transfer(
                    parent_run=run,
                    course_id=course_id,
                    version_id=version_id,
                    chapter=chapter,
                    core=core,
                    selected_anchors=selected,
                    source_hashes=source_hashes,
                    model=review_model,
                    prompt_version=prompt_version,
                )
                review_run_ids[core.key] = child_id
                return findings

            assessment = AssessmentService(
                generation_service=self.workflow.generation,
                evidence_service=self.workflow.evidence,
                model=generation_model,
                review_model=review_model,
                anchor_loader=reload_anchors,
                outline_loader=reload_outline,
                chapter_loader=reload_chapter,
                transfer_reviewer=review_transfer,
            )
            try:
                blueprints = await assessment.build_chapter_exercise_bank(
                    course_id, version_id, chapter_key, anchor_ids
                )
                records = tuple(
                    self._exercise_record(
                        course_id=course_id,
                        version_id=version_id,
                        chapter=chapter,
                        blueprint=blueprint,
                        parent_run_id=str(run.id),
                        review_run_ids=(
                            (review_run_ids[blueprint.key],)
                            if blueprint.key in review_run_ids
                            else ()
                        ),
                    )
                    for blueprint in blueprints
                )
                await self._persist_atomically(
                    run=run,
                    course=course,
                    version=version,
                    chapter=chapter,
                    anchors=selected,
                    exercises=records,
                )
                return await self._load_exercises_for_run(
                    run=run, version_id=version_id, chapter=chapter
                )
            except Exception as exc:
                await self.workflow.fail_run(run, str(exc))
                raise


__all__ = [
    "ExerciseWorkflowService",
    "canonical_exercise_output",
    "exercise_generation_claim_args",
    "exercise_record_id",
]
