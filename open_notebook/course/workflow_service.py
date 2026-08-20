"""Replay-safe worker-side orchestration for Course generation records."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import Source
from open_notebook.exceptions import NotFoundError

from . import state_machine as sm
from .contracts import (
    ChapterArtifact,
    CourseOutlineArtifact,
    ModelSelection,
    ReviewArtifact,
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
    CourseValidationFinding,
    CourseVersion,
    Lab,
)

_escalation_locks: dict[str, asyncio.Lock] = {}
_escalation_locks_guard = asyncio.Lock()


async def _escalation_lock_for(parent_run_id: str) -> asyncio.Lock:
    async with _escalation_locks_guard:
        return _escalation_locks.setdefault(parent_run_id, asyncio.Lock())


def _artifact_hash(artifact: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            artifact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def generation_input_hash(
    *,
    course_id: str,
    stage: str,
    command_args: dict[str, Any],
    model: ModelSelection,
    prompt_version: str,
    anchor_ids: list[str],
    source_hashes: dict[str, str],
    course_version_id: str | None = None,
    chapter_id: str | None = None,
    chapter_key: str | None = None,
) -> str:
    """Canonical claim shared by the API submitter and authoritative worker."""

    return _artifact_hash(
        {
            "course_id": course_id,
            "stage": stage,
            "course_version_id": course_version_id,
            "chapter_id": chapter_id,
            "chapter_key": chapter_key,
            "prompt_version": prompt_version,
            "model": model.model_dump(mode="json"),
            # Ordering is intentional: changing evidence order changes the prompt.
            "anchor_ids": anchor_ids,
            "source_hashes": dict(sorted(source_hashes.items())),
            "stable_args": command_args,
        }
    )


def artifact_replay_hash(run: CourseGenerationRun) -> str:
    """Run-scoped immutable artifact identity; logical dedupe remains separate."""

    return _artifact_hash(
        {
            "logical_input_hash": run.input_hash,
            "run_id": str(run.id),
        }
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_evidence_output(
    anchors: list[CourseEvidenceAnchor],
) -> list[dict[str, Any]]:
    """Return only immutable anchor content in one stable semantic order."""

    ordered = sorted(
        anchors,
        key=lambda anchor: (
            anchor.locator.source_id,
            anchor.locator.kind,
            anchor.locator.index,
            anchor.locator.block_key,
            anchor.anchor_id,
        ),
    )
    return [
        anchor.model_dump(
            mode="json",
            include={"anchor_id", "locator", "quote_sha256", "source_role"},
        )
        for anchor in ordered
    ]


def _canonical_review_output(
    run: CourseGenerationRun,
    findings: list[ValidationFinding],
) -> list[dict[str, Any]]:
    """Exclude mutable resolution state and normalize the immutable reviewer ID."""

    payloads: list[dict[str, Any]] = []
    for finding in findings:
        payload = finding.model_dump(
            mode="json",
            exclude={"status", "resolution_reason", "reviewer_run_id"},
        )
        payload["reviewer_run_id"] = str(run.id)
        payloads.append(payload)
    return sorted(payloads, key=_canonical_json)


def _canonical_escalation_output(
    run: CourseGenerationRun,
    findings: list[ValidationFinding],
) -> list[dict[str, Any]]:
    """Keep the immutable raw Sol decision, including status and rationale."""

    payloads: list[dict[str, Any]] = []
    for finding in findings:
        payload = finding.model_dump(mode="json", exclude={"reviewer_run_id"})
        payload["reviewer_run_id"] = str(run.id)
        payloads.append(payload)
    return sorted(payloads, key=_canonical_json)


def _rows(model: type[Any], result: Any) -> list[Any]:
    values = result if isinstance(result, list) else [result] if result else []
    return [model(**row) for row in values if isinstance(row, dict)]


@dataclass(frozen=True)
class ChapterPromotionSnapshot:
    """Exact promoted-current inputs used by facades and promotion transactions."""

    current: Chapter | None
    succeeded_run_ids: tuple[str, ...]
    manual_chapter_ids: tuple[str, ...]


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
        if not command_id or (run.command is not None and run.command != command_id):
            raise ValueError("Course generation run command binding mismatch")
        if run.status not in {
            sm.RunStatus.QUEUED,
            sm.RunStatus.RUNNING,
            sm.RunStatus.SUCCEEDED,
        }:
            raise ValueError("Course generation run is terminal")
        return run

    @staticmethod
    async def activate_run(
        run: CourseGenerationRun, command_id: str
    ) -> CourseGenerationRun:
        """Atomically bind an unbound run and enter running after preflight."""

        if run.status == sm.RunStatus.SUCCEEDED:
            if run.command != command_id:
                raise ValueError("Course generation run command binding mismatch")
            return run
        rows = await repo_query(
            """
            UPDATE $run_id
            SET command = $command_id,
                status = IF status = 'queued' THEN 'running' ELSE status END
            WHERE (command = NONE OR command = $command_id)
              AND status IN ['queued', 'running']
            RETURN AFTER;
            """,
            {
                "run_id": ensure_record_id(str(run.id)),
                "command_id": ensure_record_id(command_id),
            },
        )
        claimed = _rows(CourseGenerationRun, rows)
        if not claimed:
            current = await CourseGenerationRun.get(str(run.id))
            if current.command != command_id:
                raise ValueError("Course generation run command binding mismatch")
            raise ValueError("Course generation run is no longer active")
        run.command = claimed[0].command
        run.status = claimed[0].status
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
    def validate_run_claim(
        run: CourseGenerationRun,
        *,
        command_args: dict[str, Any],
        model: ModelSelection,
        prompt_version: str,
        anchor_ids: list[str],
        source_hashes: dict[str, str],
    ) -> None:
        expected = generation_input_hash(
            course_id=run.course,
            stage=run.stage,
            command_args=command_args,
            model=model,
            prompt_version=prompt_version,
            anchor_ids=anchor_ids,
            source_hashes=source_hashes,
            course_version_id=run.course_version,
            # A chapter-content run binds its output Chapter only after the
            # immutable request claim has been created. Review runs, by
            # contrast, claim one pre-existing Chapter as an input.
            chapter_id=run.chapter if run.stage == "review" else None,
            chapter_key=run.chapter_key,
        )
        if run.input_hash != expected:
            raise ValueError("Course generation run claim hash mismatch")

    @staticmethod
    async def complete_run(
        run: CourseGenerationRun, output: dict[str, Any] | list[Any]
    ) -> None:
        output_hash = _artifact_hash({"output": output})
        if run.status == sm.RunStatus.SUCCEEDED:
            CourseWorkflowService.verify_completed_output(run, output)
            return
        if run.status != sm.RunStatus.RUNNING:
            raise ValueError("Course generation run is no longer active")
        sm.transition("run", run.status, sm.RunStatus.SUCCEEDED)
        try:
            rows = await repo_query(
                """
                UPDATE $run_id
                SET status = 'succeeded',
                    output_hash = $output_hash,
                    error_message = NONE
                WHERE status = 'running'
                RETURN AFTER;
                """,
                {
                    "run_id": ensure_record_id(str(run.id)),
                    "output_hash": output_hash,
                },
            )
        except RuntimeError:
            rows = []
        completed = _rows(CourseGenerationRun, rows)
        current = (
            completed[0]
            if completed
            else await CourseGenerationRun.get(str(run.id))
        )
        run.status = current.status
        run.output_hash = current.output_hash
        run.error_message = current.error_message
        if current.status == sm.RunStatus.SUCCEEDED:
            CourseWorkflowService.verify_completed_output(run, output)
            return
        raise ValueError("Course generation run is no longer active")

    @staticmethod
    async def complete_chapter_run(
        *,
        run: CourseGenerationRun,
        chapter: Chapter,
        artifact: ChapterArtifact,
        expected_version_updated: datetime | None,
    ) -> None:
        """Atomically promote a Chapter run and refresh its stable-key links."""

        if chapter.id is None:
            raise ValueError("Generated chapter is not persisted")
        if run.chapter != str(chapter.id):
            raise ValueError("Course generation run chapter binding mismatch")
        output = chapter.artifact or {}
        output_hash = _artifact_hash({"output": output})
        if run.status == sm.RunStatus.SUCCEEDED:
            CourseWorkflowService.verify_completed_output(run, output)
            return
        if run.status != sm.RunStatus.RUNNING:
            raise ValueError("Course generation run is no longer active")
        sm.transition("run", run.status, sm.RunStatus.SUCCEEDED)
        siblings = await CourseVersion.chapters(chapter.course_version)
        promotion = await CourseWorkflowService.chapter_promotion_snapshot(
            course_id=run.course,
            version_id=chapter.course_version,
            chapter_key=chapter.chapter_key,
            chapters=siblings,
        )
        refresh_stable_links = (
            promotion.current is None
            or chapter.version_no >= promotion.current.version_no
        )
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
        try:
            await repo_query(
                """
                BEGIN TRANSACTION;
                LET $mutable_version = (
                    UPDATE course_version
                    SET updated = time::now()
                    WHERE id = $version_id
                      AND course = $course_id
                      AND status = 'generating'
                      AND time::micros(updated) =
                          time::micros($expected_version_updated)
                    RETURN VALUE id
                );
                IF array::len($mutable_version) != 1 {
                    THROW 'Course version is no longer mutable'
                };
                LET $unexpected_succeeded_runs = (
                    SELECT VALUE id FROM course_generation_run
                    WHERE course = $course_id
                      AND course_version = $version_id
                      AND chapter_key = $chapter_key
                      AND stage = 'chapter_content'
                      AND status = 'succeeded'
                      AND id NOT IN $known_succeeded_run_ids
                );
                IF array::len($unexpected_succeeded_runs) != 0 {
                    THROW 'Course chapter promotion snapshot changed'
                };
                LET $unexpected_manual_chapters = (
                    SELECT VALUE id FROM chapter
                    WHERE course_version = $version_id
                      AND chapter_key = $chapter_key
                      AND input_hash = NONE
                      AND id NOT IN $known_manual_chapter_ids
                );
                IF array::len($unexpected_manual_chapters) != 0 {
                    THROW 'Course chapter promotion snapshot changed'
                };
                LET $completed = (
                    UPDATE course_generation_run
                    SET status = 'succeeded',
                        output_hash = $output_hash,
                        error_message = NONE
                    WHERE id = $run_id
                      AND status = 'running'
                      AND chapter = $chapter_id
                    RETURN AFTER
                );
                IF array::len($completed) != 1 {
                    THROW 'Course generation run completion conflict'
                };
                IF $refresh_stable_links {
                    UPDATE course_note SET orphan_status =
                        IF block_key != NONE AND block_key NOT IN $block_keys
                        THEN 'orphaned' ELSE 'active' END
                    WHERE course = $course_id AND chapter_key = $chapter_key;
                    UPDATE progress SET orphan_status =
                        IF block_key != NONE AND block_key NOT IN $block_keys
                        THEN 'orphaned' ELSE 'active' END
                    WHERE course = $course_id AND chapter_key = $chapter_key;
                    UPDATE attempt SET orphan_status =
                        IF exercise_key != NONE AND exercise_key NOT IN $exercise_keys
                        THEN 'orphaned' ELSE 'active' END
                    WHERE course = $course_id AND chapter_key = $chapter_key;
                };
                COMMIT TRANSACTION;
                """,
                {
                    "run_id": ensure_record_id(str(run.id)),
                    "course_id": ensure_record_id(run.course),
                    "version_id": ensure_record_id(chapter.course_version),
                    "expected_version_updated": expected_version_updated,
                    "chapter_id": ensure_record_id(str(chapter.id)),
                    "chapter_key": chapter.chapter_key,
                    "output_hash": output_hash,
                    "block_keys": sorted(block_keys),
                    "exercise_keys": sorted(exercise_keys),
                    "known_succeeded_run_ids": [
                        ensure_record_id(run_id)
                        for run_id in promotion.succeeded_run_ids
                    ],
                    "known_manual_chapter_ids": [
                        ensure_record_id(chapter_id)
                        for chapter_id in promotion.manual_chapter_ids
                    ],
                    "refresh_stable_links": refresh_stable_links,
                },
            )
        except RuntimeError:
            current = await CourseGenerationRun.get(str(run.id))
            run.status = current.status
            run.output_hash = current.output_hash
            run.error_message = current.error_message
            run.chapter = current.chapter
            if current.status == sm.RunStatus.SUCCEEDED:
                if current.chapter != str(chapter.id):
                    raise ValueError(
                        "Course generation run chapter binding mismatch"
                    )
                CourseWorkflowService.verify_completed_output(run, output)
                return
            raise ValueError("Course generation run is no longer active") from None
        run.status = sm.RunStatus.SUCCEEDED
        run.output_hash = output_hash
        run.error_message = None

    @staticmethod
    def verify_completed_output(
        run: CourseGenerationRun, output: dict[str, Any] | list[Any]
    ) -> None:
        expected = _artifact_hash({"output": output})
        if run.output_hash is None or run.output_hash != expected:
            raise ValueError("Course generation run output hash mismatch")

    @staticmethod
    async def fail_run(run: CourseGenerationRun, message: str) -> None:
        if run.status != sm.RunStatus.RUNNING:
            return
        sm.transition("run", run.status, sm.RunStatus.FAILED)
        error_message = message[:1000]
        try:
            rows = await repo_query(
                """
                UPDATE $run_id
                SET status = 'failed', error_message = $error_message
                WHERE status = 'running'
                RETURN AFTER;
                """,
                {
                    "run_id": ensure_record_id(str(run.id)),
                    "error_message": error_message,
                },
            )
        except RuntimeError:
            rows = []
        failed = _rows(CourseGenerationRun, rows)
        current = (
            failed[0]
            if failed
            else await CourseGenerationRun.get(str(run.id))
        )
        run.status = current.status
        run.output_hash = current.output_hash
        run.error_message = current.error_message

    @staticmethod
    async def fail_run_reference(
        *, run_id: str, command_id: str, message: str
    ) -> None:
        if not command_id:
            return
        await repo_query(
            """
            UPDATE $run_id
            SET error_message = $message,
                status = IF status = 'queued' THEN 'cancelled' ELSE 'failed' END
            WHERE status IN ['queued', 'running']
              AND (command = NONE OR command = $command_id);
            """,
            {
                "run_id": ensure_record_id(run_id),
                "command_id": ensure_record_id(command_id),
                "message": message[:1000],
            },
        )

    @staticmethod
    async def bind_run_chapter(
        run: CourseGenerationRun, chapter: Chapter
    ) -> CourseGenerationRun:
        """Bind a generated artifact without making it current before success."""

        if chapter.id is None:
            raise ValueError("Generated chapter is not persisted")
        chapter_id = str(chapter.id)
        if run.status == sm.RunStatus.SUCCEEDED:
            if run.chapter != chapter_id:
                raise ValueError("Course generation run chapter binding mismatch")
            return run
        rows = await repo_query(
            """
            UPDATE $run_id SET chapter = $chapter_id
            WHERE status = 'running'
              AND command = $command_id
              AND (chapter = NONE OR chapter = $chapter_id)
            RETURN AFTER;
            """,
            {
                "run_id": ensure_record_id(str(run.id)),
                "chapter_id": ensure_record_id(chapter_id),
                "command_id": ensure_record_id(str(run.command)),
            },
        )
        claimed = _rows(CourseGenerationRun, rows)
        if not claimed:
            current = await CourseGenerationRun.get(str(run.id))
            if current.chapter != chapter_id:
                raise ValueError("Course generation run chapter binding mismatch")
            if current.status not in {
                sm.RunStatus.RUNNING,
                sm.RunStatus.SUCCEEDED,
            }:
                raise ValueError("Course generation run is no longer active")
            run.chapter = current.chapter
            run.status = current.status
            run.output_hash = current.output_hash
            return run
        run.chapter = claimed[0].chapter
        run.status = claimed[0].status
        run.output_hash = claimed[0].output_hash
        return run

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
    def validate_approved_version(
        course: Course, version: CourseVersion
    ) -> CourseOutlineArtifact:
        """Validate the complete immutable approval contract in one place."""

        if (
            not course.outline_version_id
            or str(version.id) != course.outline_version_id
            or version.course != course.id
            or version.approved_at is None
        ):
            raise ValueError("Current outline is not approved")
        if version.confirmation != "确认大纲" or version.outline_artifact is None:
            raise ValueError("Current outline approval is invalid")
        if version.outline_hash != _artifact_hash(version.outline_artifact):
            raise ValueError("Approved outline hash changed")
        return CourseOutlineArtifact.model_validate(version.outline_artifact)

    @classmethod
    async def approved_version(
        cls, course: Course
    ) -> tuple[CourseVersion, CourseOutlineArtifact]:
        if not course.outline_version_id:
            raise ValueError("Course has no current outline version")
        version = await CourseVersion.get(course.outline_version_id)
        return version, cls.validate_approved_version(course, version)

    @staticmethod
    async def chapter_promotion_snapshot(
        *,
        course_id: str,
        version_id: str,
        chapter_key: str,
        chapters: list[Chapter] | None = None,
    ) -> ChapterPromotionSnapshot:
        """Build one exact promotion snapshot for reads and transaction guards."""

        candidates = [
            chapter
            for chapter in (
                chapters
                if chapters is not None
                else await CourseVersion.chapters(version_id)
            )
            if chapter.course_version == version_id
            and chapter.chapter_key == chapter_key
            and chapter.id is not None
        ]
        generated = [
            chapter for chapter in candidates if chapter.input_hash is not None
        ]
        promoted_ids: set[str] = set()
        succeeded_run_ids: set[str] = set()
        if generated:
            rows = await repo_query(
                """
                SELECT * FROM course_generation_run
                WHERE course = $course
                  AND course_version = $version
                  AND chapter_key = $chapter_key
                  AND stage = 'chapter_content';
                """,
                {
                    "course": ensure_record_id(course_id),
                    "version": ensure_record_id(version_id),
                    "chapter_key": chapter_key,
                },
            )
            runs = _rows(CourseGenerationRun, rows)
            generated_by_id = {str(chapter.id): chapter for chapter in generated}
            for run in runs:
                if (
                    run.course != course_id
                    or run.course_version != version_id
                    or run.chapter_key != chapter_key
                    or run.stage != "chapter_content"
                    or run.status != sm.RunStatus.SUCCEEDED
                ):
                    continue
                succeeded_run_ids.add(str(run.id))
                replay_hash = artifact_replay_hash(run)
                if run.chapter is None:
                    legacy_matches = [
                        chapter
                        for chapter in generated
                        if chapter.input_hash == replay_hash
                        and run.output_hash
                        == _artifact_hash({"output": chapter.artifact or {}})
                    ]
                    if len(legacy_matches) == 1:
                        promoted_ids.add(str(legacy_matches[0].id))
                    continue
                chapter = generated_by_id.get(run.chapter)
                if chapter is None or chapter.input_hash != replay_hash:
                    continue
                expected = _artifact_hash({"output": chapter.artifact or {}})
                if run.output_hash == expected:
                    promoted_ids.add(run.chapter)

        eligible = [
            chapter
            for chapter in candidates
            if chapter.input_hash is None or str(chapter.id) in promoted_ids
        ]
        return ChapterPromotionSnapshot(
            current=max(eligible, key=lambda chapter: chapter.version_no)
            if eligible
            else None,
            succeeded_run_ids=tuple(sorted(succeeded_run_ids)),
            manual_chapter_ids=tuple(
                sorted(
                    str(chapter.id)
                    for chapter in candidates
                    if chapter.input_hash is None
                )
            ),
        )

    @staticmethod
    async def resolve_current_chapter(
        *,
        course_id: str,
        version_id: str,
        chapter_key: str,
        chapters: list[Chapter] | None = None,
    ) -> Chapter:
        """Return the latest Chapter fully promoted by a successful content run."""

        snapshot = await CourseWorkflowService.chapter_promotion_snapshot(
            course_id=course_id,
            version_id=version_id,
            chapter_key=chapter_key,
            chapters=chapters,
        )
        if snapshot.current is None:
            raise NotFoundError("Chapter not found")
        return snapshot.current

    @staticmethod
    async def authoritative_review_findings(
        *,
        course_id: str,
        version_id: str,
        chapter: Chapter,
    ) -> tuple[CourseGenerationRun | None, list[CourseValidationFinding]]:
        """Return only the newest successful parent-review finding set."""

        if chapter.id is None:
            raise ValueError("Chapter is not persisted")
        rows = await repo_query(
            "SELECT * FROM course_generation_run "
            "WHERE course = $course AND course_version = $version "
            "AND chapter = $chapter AND chapter_key = $chapter_key "
            "AND stage = 'review' "
            "ORDER BY created DESC, id DESC;",
            {
                "course": ensure_record_id(course_id),
                "version": ensure_record_id(version_id),
                "chapter": ensure_record_id(str(chapter.id)),
                "chapter_key": chapter.chapter_key,
            },
        )
        candidates = _rows(CourseGenerationRun, rows)
        for run in candidates:
            if (
                run.course != course_id
                or run.course_version != version_id
                or run.chapter != str(chapter.id)
                or run.chapter_key != chapter.chapter_key
                or run.stage != "review"
            ):
                continue
            # Any newer review attempt supersedes history immediately. A
            # failed or in-flight attempt must never reactivate older rows.
            if run.status != sm.RunStatus.SUCCEEDED:
                return None, []
            finding_rows = _rows(
                CourseValidationFinding,
                await repo_query(
                    "SELECT * FROM course_validation_finding "
                    "WHERE generation_run = $run ORDER BY id;",
                    {"run": ensure_record_id(str(run.id))},
                ),
            )
            artifacts = [
                ValidationFinding.model_validate(row.finding)
                for row in finding_rows
            ]
            CourseWorkflowService.verify_completed_output(
                run, _canonical_review_output(run, artifacts)
            )
            return run, finding_rows
        return None, []

    async def build_evidence(
        self,
        *,
        run: CourseGenerationRun,
        command_id: str,
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
        self.evidence._assert_role(course, source_id, role)
        source_hash = await self._source_hash(source_id)
        evidence_model = ModelSelection(adapter="open_notebook", model="docling")
        self.validate_run_claim(
            run,
            command_args={
                "course_id": course_id,
                "source_id": source_id,
                "role": role,
            },
            model=evidence_model,
            prompt_version="evidence-v1",
            anchor_ids=[],
            source_hashes={source_id: source_hash},
        )
        await self.activate_run(run, command_id)
        if run.status == sm.RunStatus.SUCCEEDED:
            anchors = _rows(
                CourseEvidenceAnchor,
                await repo_query(
                    "SELECT * FROM course_evidence_anchor "
                    "WHERE course = $course AND source = $source "
                    "AND is_current = true "
                    "ORDER BY locator.index, locator.block_key, anchor_id;",
                    {
                        "course": ensure_record_id(course_id),
                        "source": ensure_record_id(source_id),
                    },
                ),
            )
            output = _canonical_evidence_output(anchors)
            self.verify_completed_output(run, output)
            return anchors
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
        await self.complete_run(run, _canonical_evidence_output(anchors))
        return anchors

    async def generate_outline(
        self,
        *,
        run: CourseGenerationRun,
        command_id: str,
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
        _, source_hashes, context = await self.grounded_inputs(
            course=course, anchor_ids=anchor_ids
        )
        self.validate_run_claim(
            run,
            command_args={
                "course_id": course_id,
                "anchor_ids": anchor_ids,
                "available_lab_keys": available_lab_keys,
                "prompt_version": prompt_version,
                "model": model.model_dump(mode="json"),
            },
            model=model,
            prompt_version=prompt_version,
            anchor_ids=anchor_ids,
            source_hashes=source_hashes,
        )
        await self.activate_run(run, command_id)
        replay_hash = artifact_replay_hash(run)

        async with course_job_lock():
            existing = _rows(
                CourseVersion,
                await repo_query(
                    "SELECT * FROM course_version "
                    "WHERE course = $course AND input_hash = $hash;",
                    {"course": ensure_record_id(course_id), "hash": replay_hash},
                ),
            )
            if len(existing) > 1:
                raise ValueError("Course outline replay artifact is not unique")
            if run.status == sm.RunStatus.SUCCEEDED:
                if not existing:
                    raise ValueError("Course outline replay artifact is missing")
                version = existing[0]
                self.verify_completed_output(run, version.outline_artifact or {})
                return version
            if existing:
                version = existing[0]
                if (
                    version.approved_at is not None
                    or version.status == sm.VersionStatus.PUBLISHED
                ):
                    await self.complete_run(run, version.outline_artifact or {})
                    return version
            else:
                if course.status in {
                    sm.CourseStatus.OUTLINE_APPROVED,
                    sm.CourseStatus.GENERATING,
                }:
                    raise ValueError("The current approved outline cannot be overwritten")
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
                    input_hash=replay_hash,
                )
                await version.save()

        current = (
            await CourseVersion.get(course.outline_version_id)
            if course.outline_version_id
            else None
        )
        if current is None or version.version_no >= current.version_no:
            course.outline = version.outline_artifact
            course.outline_version_id = str(version.id)
            if course.status in {sm.CourseStatus.DRAFT, sm.CourseStatus.FAILED}:
                course.status = sm.transition(
                    "course", course.status, sm.CourseStatus.INDEXING
                )
            elif course.status == sm.CourseStatus.OUTLINE_READY:
                course.status = sm.transition(
                    "course", course.status, sm.CourseStatus.INDEXING
                )
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
    async def advance_chapter_to_reviewing(chapter: Chapter) -> None:
        if chapter.status == sm.ChapterStatus.DRAFT:
            await chapter.transition_to(sm.ChapterStatus.GENERATING)
        if chapter.status == sm.ChapterStatus.GENERATING:
            await chapter.transition_to(sm.ChapterStatus.REVIEWING)
        if chapter.status != sm.ChapterStatus.REVIEWING:
            raise ValueError("Chapter replay is not in a mutable generation state")

    async def generate_chapter(
        self,
        *,
        run: CourseGenerationRun,
        command_id: str,
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
        chapter_no, proposal = self._outline_chapter(outline, chapter_key)
        if set(anchor_ids) != set(proposal.anchor_ids):
            raise ValueError("Chapter evidence must match the approved outline")
        _, source_hashes, context = await self.grounded_inputs(
            course=course, anchor_ids=anchor_ids
        )
        self.validate_run_claim(
            run,
            command_args={
                "course_id": course_id,
                "chapter_key": chapter_key,
                "anchor_ids": anchor_ids,
                "prompt_version": prompt_version,
                "model": model.model_dump(mode="json"),
            },
            model=model,
            prompt_version=prompt_version,
            anchor_ids=anchor_ids,
            source_hashes=source_hashes,
        )
        await self.activate_run(run, command_id)
        replay_hash = artifact_replay_hash(run)
        async with course_job_lock():
            existing = _rows(
                Chapter,
                await repo_query(
                    "SELECT * FROM chapter "
                    "WHERE course_version = $version AND input_hash = $hash;",
                    {
                        "version": ensure_record_id(str(version.id)),
                        "hash": replay_hash,
                    },
                ),
            )
            if len(existing) > 1:
                raise ValueError("Chapter replay artifact is not unique")
            if run.status == sm.RunStatus.SUCCEEDED:
                if not existing:
                    raise ValueError("Chapter replay artifact is missing")
                chapter = existing[0]
                if run.chapter is not None and run.chapter != str(chapter.id):
                    raise ValueError("Course generation run chapter binding mismatch")
                self.verify_completed_output(run, chapter.artifact or {})
                return chapter
            if existing:
                chapter = existing[0]
            else:
                if version.status == sm.VersionStatus.PUBLISHED:
                    raise ValueError("Published Course versions are immutable")
                artifact = await self.generation.generate_chapter(
                    course_id=course_id,
                    chapter_key=chapter_key,
                    anchor_ids=anchor_ids,
                    evidence=context,
                    approved_lab_keys=set(proposal.lab_keys),
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
                    artifact=artifact.model_dump(mode="json", by_alias=True),
                    input_hash=replay_hash,
                    citations=[{"anchor_id": item} for item in artifact.citations],
                )
                await chapter.save()
            await self.bind_run_chapter(run, chapter)
            if run.status == sm.RunStatus.SUCCEEDED:
                self.verify_completed_output(run, chapter.artifact or {})
                return chapter
            await self.advance_chapter_to_reviewing(chapter)

        await self._ensure_labs(version, chapter)
        artifact = ChapterArtifact.model_validate(chapter.artifact)
        if version.status == sm.VersionStatus.DRAFT:
            await version.transition_to(sm.VersionStatus.GENERATING)
        if course.status == sm.CourseStatus.OUTLINE_APPROVED:
            await course.transition_to(sm.CourseStatus.GENERATING)
        await self.complete_chapter_run(
            run=run,
            chapter=chapter,
            artifact=artifact,
            expected_version_updated=version.updated,
        )
        return chapter

    @staticmethod
    async def _persist_findings(
        *,
        run: CourseGenerationRun,
        course_id: str,
        version_id: str,
        chapter: Chapter,
        findings: list[ValidationFinding],
        completion_output: list[dict[str, Any]] | None = None,
    ) -> None:
        """Atomically replace a run's findings and optionally terminalize it."""

        records: list[dict[str, Any]] = []
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
            records.append(
                {
                    "id": ensure_record_id(
                        f"course_validation_finding:{identity}"
                    ),
                    "content": {
                        "course": ensure_record_id(course_id),
                        "course_version": ensure_record_id(version_id),
                        "chapter": ensure_record_id(str(chapter.id)),
                        "generation_run": ensure_record_id(str(run.id)),
                        "chapter_key": chapter.chapter_key,
                        "finding": payload,
                        "severity": finding.severity,
                        "status": finding.status,
                        "resolution_reason": finding.resolution_reason,
                    },
                }
            )

        completion = ""
        variables: dict[str, Any] = {
            "run": ensure_record_id(str(run.id)),
            "findings": records,
        }
        output_hash: str | None = None
        if completion_output is not None:
            output_hash = _artifact_hash({"output": completion_output})
            variables["output_hash"] = output_hash
            completion = """
                LET $completed = (
                    UPDATE $run
                    SET status = 'succeeded',
                        output_hash = $output_hash,
                        error_message = NONE
                    WHERE status = 'running'
                    RETURN AFTER
                );
                IF array::len($completed) != 1 {
                    THROW 'Course generation run completion conflict'
                };
            """

        try:
            await repo_query(
                f"""
                BEGIN TRANSACTION;
                DELETE course_validation_finding WHERE generation_run = $run;
                FOR $finding IN $findings {{
                    UPSERT $finding.id CONTENT $finding.content;
                }};
                {completion}
                COMMIT TRANSACTION;
                """,
                variables,
            )
        except RuntimeError:
            if completion_output is None:
                raise
            current = await CourseGenerationRun.get(str(run.id))
            run.status = current.status
            run.output_hash = current.output_hash
            run.error_message = current.error_message
            if current.status == sm.RunStatus.SUCCEEDED:
                CourseWorkflowService.verify_completed_output(
                    run, completion_output
                )
                return
            raise ValueError(
                "Course generation run is no longer active"
            ) from None

        if completion_output is not None:
            run.status = sm.RunStatus.SUCCEEDED
            run.output_hash = output_hash
            run.error_message = None

    @staticmethod
    async def _finding_rows(run: CourseGenerationRun) -> list[ValidationFinding]:
        rows = await repo_query(
            "SELECT * FROM course_validation_finding "
            "WHERE generation_run = $run ORDER BY id;",
            {"run": ensure_record_id(str(run.id))},
        )
        return [
            ValidationFinding.model_validate(row["finding"])
            for row in rows
        ]

    async def _inline_escalation(
        self,
        *,
        parent_run: CourseGenerationRun,
        course_id: str,
        version_id: str,
        chapter: Chapter,
        findings: list[ValidationFinding],
        selected_anchors: list[CourseEvidenceAnchor],
        source_hashes: dict[str, str],
        model: ModelSelection,
        prompt_version: str,
    ) -> list[ValidationFinding]:
        """Run one replay-safe escalation child inline with no nested queue."""

        lock = await _escalation_lock_for(str(parent_run.id))
        async with lock:
            return await self._inline_escalation_unlocked(
                parent_run=parent_run,
                course_id=course_id,
                version_id=version_id,
                chapter=chapter,
                findings=findings,
                selected_anchors=selected_anchors,
                source_hashes=source_hashes,
                model=model,
                prompt_version=prompt_version,
            )

    async def _inline_escalation_unlocked(
        self,
        *,
        parent_run: CourseGenerationRun,
        course_id: str,
        version_id: str,
        chapter: Chapter,
        findings: list[ValidationFinding],
        selected_anchors: list[CourseEvidenceAnchor],
        source_hashes: dict[str, str],
        model: ModelSelection,
        prompt_version: str,
    ) -> list[ValidationFinding]:
        """Execute one inline child while holding the parent-run lock."""

        known_anchor_ids = {anchor.anchor_id for anchor in selected_anchors}
        eligible = self.generation.escalation_candidates(
            findings, known_anchor_ids=known_anchor_ids
        )
        if not eligible:
            return findings
        required_anchor_ids = list(
            dict.fromkeys(
                anchor_id
                for finding in eligible
                for anchor_id in finding.anchor_ids
            )
        )
        by_anchor = {anchor.anchor_id: anchor for anchor in selected_anchors}
        evidence_by_anchor = {
            anchor_id: by_anchor[anchor_id].locator.quote
            for anchor_id in required_anchor_ids
        }
        required_source_ids = {
            by_anchor[anchor_id].source for anchor_id in required_anchor_ids
        }
        required_source_hashes = {
            source_id: source_hashes[source_id]
            for source_id in sorted(required_source_ids)
        }
        eligible_payload = sorted(
            (
                finding.model_dump(mode="json", exclude={"reviewer_run_id"})
                for finding in eligible
            ),
            key=_canonical_json,
        )
        child_args = {
            "parent_run_id": str(parent_run.id),
            "course_id": course_id,
            "chapter_key": chapter.chapter_key,
            "prompt_version": prompt_version,
            "model": model.model_dump(mode="json"),
            "findings": eligible_payload,
            "anchor_ids": required_anchor_ids,
        }
        input_hash = generation_input_hash(
            course_id=course_id,
            stage="escalation",
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
        child = children[0] if children else None
        if child is None:
            child_id = f"course_generation_run:{input_hash[:48]}_1"
            payload = {
                "course": ensure_record_id(course_id),
                "course_version": ensure_record_id(version_id),
                "chapter": ensure_record_id(str(chapter.id)),
                "chapter_key": chapter.chapter_key,
                "stage": "escalation",
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
            raise ValueError("Course escalation run could not be created")
        if (
            child.course != course_id
            or child.course_version != version_id
            or child.chapter != str(chapter.id)
            or child.chapter_key != chapter.chapter_key
            or child.stage != "escalation"
            or child.adapter != model.adapter
            or child.model != model.model
            or child.reasoning_effort != model.reasoning_effort
            or child.prompt_version != prompt_version
            or child.input_hash != input_hash
            or child.command is not None
        ):
            raise ValueError("Course escalation run ownership mismatch")

        if child.status == sm.RunStatus.SUCCEEDED:
            raw_findings = await self._finding_rows(child)
            self.verify_completed_output(
                child, _canonical_escalation_output(child, raw_findings)
            )
            return self.generation.merge_escalation_findings(
                findings,
                ReviewArtifact(findings=raw_findings),
                known_anchor_ids=known_anchor_ids,
            )
        if child.status in {sm.RunStatus.FAILED, sm.RunStatus.CANCELLED}:
            raise ValueError("Course escalation run is terminal")
        if child.status == sm.RunStatus.QUEUED:
            claimed = _rows(
                CourseGenerationRun,
                await repo_query(
                    "UPDATE $run_id SET status = 'running' "
                    "WHERE status = 'queued' AND command = NONE RETURN AFTER;",
                    {"run_id": ensure_record_id(str(child.id))},
                ),
            )
            if claimed:
                child = claimed[0]
            else:
                child = await CourseGenerationRun.get(str(child.id))
        if child.status != sm.RunStatus.RUNNING:
            raise ValueError("Course escalation run is no longer active")

        try:
            checkpointed = await self._finding_rows(child)
            if checkpointed:
                merged = self.generation.merge_escalation_findings(
                    findings,
                    ReviewArtifact(findings=checkpointed),
                    known_anchor_ids=known_anchor_ids,
                )
                await self._persist_findings(
                    run=child,
                    course_id=course_id,
                    version_id=version_id,
                    chapter=chapter,
                    findings=checkpointed,
                    completion_output=_canonical_escalation_output(
                        child, checkpointed
                    ),
                )
                return merged
            async with course_job_lock():
                raw = await self.generation.escalate_raw(
                    course_id=course_id,
                    chapter_key=chapter.chapter_key,
                    findings=findings,
                    evidence_by_anchor=evidence_by_anchor,
                    model=model,
                    prompt_version=prompt_version,
                )
            merged = self.generation.merge_escalation_findings(
                findings, raw, known_anchor_ids=known_anchor_ids
            )
            await self._persist_findings(
                run=child,
                course_id=course_id,
                version_id=version_id,
                chapter=chapter,
                findings=raw.findings,
                completion_output=_canonical_escalation_output(
                    child, raw.findings
                ),
            )
            return merged
        except Exception as exc:
            await self.fail_run(child, str(exc))
            raise

    async def review_chapter(
        self,
        *,
        run: CourseGenerationRun,
        command_id: str,
        course_id: str,
        chapter_key: str,
        anchor_ids: list[str],
        model: ModelSelection,
        escalation_model: ModelSelection,
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
        selected_anchors, source_hashes, _ = await self.grounded_inputs(
            course=course, anchor_ids=anchor_ids
        )
        self.validate_run_claim(
            run,
            command_args={
                "course_id": course_id,
                "chapter_key": chapter_key,
                "anchor_ids": anchor_ids,
                "prompt_version": prompt_version,
                "model": model.model_dump(mode="json"),
                "escalation_model": escalation_model.model_dump(mode="json"),
            },
            model=model,
            prompt_version=prompt_version,
            anchor_ids=anchor_ids,
            source_hashes=source_hashes,
        )
        await self.activate_run(run, command_id)
        if run.status == sm.RunStatus.SUCCEEDED:
            if not run.chapter:
                raise ValueError("Course review replay chapter is missing")
            chapter = await Chapter.get(run.chapter)
            if (
                chapter.course_version != str(version.id)
                or chapter.chapter_key != chapter_key
            ):
                raise ValueError("Course chapter review run is stale")
            findings = await self._finding_rows(run)
            output = _canonical_review_output(run, findings)
            self.verify_completed_output(run, output)
            return chapter, findings
        chapters = await CourseVersion.chapters(str(version.id))
        chapter = await self.resolve_current_chapter(
            course_id=course_id,
            version_id=str(version.id),
            chapter_key=chapter_key,
            chapters=chapters,
        )
        if run.chapter != str(chapter.id):
            raise ValueError("Course chapter review run is stale")
        if chapter.status == sm.ChapterStatus.PUBLISHED:
            raise ValueError("Published chapters are immutable")
        artifact = ChapterArtifact.model_validate(chapter.artifact)

        # A manual re-review must make a formerly publishable chapter
        # non-publishable before the new findings are evaluated. Persist the
        # lifecycle reset so a worker crash cannot leave ready/passed state.
        if chapter.status in {sm.ChapterStatus.READY, sm.ChapterStatus.BLOCKED}:
            await chapter.transition_to(sm.ChapterStatus.GENERATING)
        if chapter.status == sm.ChapterStatus.GENERATING:
            await chapter.transition_to(sm.ChapterStatus.REVIEWING)
        if chapter.status != sm.ChapterStatus.REVIEWING:
            raise ValueError("Chapter is not in a reviewable state")
        if chapter.review_status != sm.ChapterReviewStatus.PENDING:
            await chapter.transition_review(sm.ChapterReviewStatus.PENDING)
        if chapter.validation_status != sm.ChapterValidationStatus.PENDING:
            await chapter.transition_validation(sm.ChapterValidationStatus.PENDING)

        existing_rows = await self._finding_rows(run)
        if run.output_hash is not None:
            findings = existing_rows
        elif existing_rows:
            # Base Luna/validator findings are the parent checkpoint. A child
            # replay must never call Luna or Sol again after it has succeeded.
            findings = existing_rows
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
                artifact, set(anchor_ids), subject=course.subject
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
            findings = [unique[key] for key in sorted(unique)]
            await self._persist_findings(
                run=run,
                course_id=course_id,
                version_id=str(version.id),
                chapter=chapter,
                findings=findings,
            )

        if self.generation.requires_escalation(
            findings,
            known_anchor_ids={anchor.anchor_id for anchor in selected_anchors},
        ):
            if chapter.review_status == sm.ChapterReviewStatus.PENDING:
                await chapter.transition_review(sm.ChapterReviewStatus.ESCALATED)
            try:
                findings = await self._inline_escalation(
                    parent_run=run,
                    course_id=course_id,
                    version_id=str(version.id),
                    chapter=chapter,
                    findings=findings,
                    selected_anchors=selected_anchors,
                    source_hashes=source_hashes,
                    model=escalation_model,
                    prompt_version=prompt_version,
                )
            except Exception:
                if chapter.review_status == sm.ChapterReviewStatus.ESCALATED:
                    await chapter.transition_review(sm.ChapterReviewStatus.FAILED)
                if chapter.validation_status == sm.ChapterValidationStatus.PENDING:
                    await chapter.transition_validation(
                        sm.ChapterValidationStatus.FAILED
                    )
                if chapter.status == sm.ChapterStatus.REVIEWING:
                    await chapter.transition_to(sm.ChapterStatus.BLOCKED)
                raise

        try:
            self.generation.assert_publishable(findings)
        except PublicationBlocked:
            if chapter.review_status == sm.ChapterReviewStatus.PENDING:
                await chapter.transition_review(sm.ChapterReviewStatus.ESCALATED)
            if chapter.status == sm.ChapterStatus.REVIEWING:
                await chapter.transition_to(sm.ChapterStatus.BLOCKED)
        else:
            if chapter.review_status in {
                sm.ChapterReviewStatus.PENDING,
                sm.ChapterReviewStatus.ESCALATED,
            }:
                await chapter.transition_review(sm.ChapterReviewStatus.PASSED)
            if chapter.validation_status == sm.ChapterValidationStatus.PENDING:
                await chapter.transition_validation(sm.ChapterValidationStatus.PASSED)
            if chapter.status == sm.ChapterStatus.REVIEWING:
                await chapter.transition_to(sm.ChapterStatus.READY)
        await self._persist_findings(
            run=run,
            course_id=course_id,
            version_id=str(version.id),
            chapter=chapter,
            findings=findings,
            completion_output=_canonical_review_output(run, findings),
        )
        return chapter, findings


__all__ = [
    "CourseWorkflowService",
    "artifact_replay_hash",
    "generation_input_hash",
]
