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
                LET $promoted_chapters = (
                    SELECT VALUE chapter FROM course_generation_run
                    WHERE course = $course_id
                      AND course_version = $version_id
                      AND chapter_key = $chapter_key
                      AND stage = 'chapter_content'
                      AND status = 'succeeded'
                      AND chapter != NONE
                );
                LET $newer_current = (
                    SELECT VALUE id FROM chapter
                    WHERE course_version = $version_id
                      AND chapter_key = $chapter_key
                      AND version_no > $chapter_version
                      AND (input_hash = NONE OR id IN $promoted_chapters)
                );
                IF array::len($newer_current) = 0 {
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
                    "chapter_id": ensure_record_id(str(chapter.id)),
                    "chapter_key": chapter.chapter_key,
                    "chapter_version": chapter.version_no,
                    "output_hash": output_hash,
                    "block_keys": sorted(block_keys),
                    "exercise_keys": sorted(exercise_keys),
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
    async def resolve_current_chapter(
        *,
        course_id: str,
        version_id: str,
        chapter_key: str,
        chapters: list[Chapter] | None = None,
    ) -> Chapter:
        """Return the latest Chapter fully promoted by a successful content run."""

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
        if not candidates:
            raise NotFoundError("Chapter not found")

        generated = [
            chapter for chapter in candidates if chapter.input_hash is not None
        ]
        promoted_ids: set[str] = set()
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
                if run.status != sm.RunStatus.SUCCEEDED:
                    continue
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
        if not eligible:
            raise NotFoundError("Chapter not found")
        return max(eligible, key=lambda chapter: chapter.version_no)

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
        command_id: str,
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
        _, source_hashes, _ = await self.grounded_inputs(
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
        if run.status == sm.RunStatus.SUCCEEDED:
            if not run.chapter:
                raise ValueError("Course review replay chapter is missing")
            chapter = await Chapter.get(run.chapter)
            if (
                chapter.course_version != str(version.id)
                or chapter.chapter_key != chapter_key
            ):
                raise ValueError("Course chapter review run is stale")
            existing_rows = await repo_query(
                "SELECT * FROM course_validation_finding "
                "WHERE generation_run = $run ORDER BY id;",
                {"run": ensure_record_id(str(run.id))},
            )
            findings = [
                ValidationFinding.model_validate(row["finding"])
                for row in existing_rows
            ]
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

        existing_rows = await repo_query(
            "SELECT * FROM course_validation_finding "
            "WHERE generation_run = $run ORDER BY id;",
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
            findings = [unique[key] for key in sorted(unique)]
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
        await self.complete_run(run, _canonical_review_output(run, findings))
        return chapter, findings


__all__ = [
    "CourseWorkflowService",
    "artifact_replay_hash",
    "generation_input_hash",
]
