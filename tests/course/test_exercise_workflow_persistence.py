from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from surrealdb import AsyncSurreal

from open_notebook.course.contracts import ModelSelection, ReviewArtifact
from open_notebook.course.evidence_service import EvidenceService
from open_notebook.course.exercise_workflow_service import (
    ExerciseWorkflowService,
    canonical_exercise_output,
    exercise_generation_claim_args,
    exercise_record_id,
)
from open_notebook.course.generation_service import CourseGenerationService
from open_notebook.course.models import CourseGenerationRun
from open_notebook.course.v2_contracts import ExerciseBankArtifact
from open_notebook.course.v2_models import CourseExercise
from open_notebook.course.workflow_service import (
    CourseWorkflowService,
    artifact_replay_hash,
    generation_input_hash,
)
from tests.course.test_chapter_exercise_bank import SequenceAdapter, _core, _source

SOURCE_HASH = "b" * 64
GENERATION_MODEL = ModelSelection(adapter="ollama", model="qwen3.5:9b")
REVIEW_MODEL = ModelSelection(adapter="ollama", model="gpt-oss:20b")


def _migration(version: str) -> str:
    return Path(f"open_notebook/database/migrations/{version}.surrealql").read_text(
        encoding="utf-8"
    )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@dataclass
class WorkflowHarness:
    database: AsyncSurreal
    service: ExerciseWorkflowService
    adapter: SequenceAdapter
    anchor_id: str
    repository: object

    async def create_parent_run(
        self,
        *,
        run_id: str,
        command_id: str,
    ) -> CourseGenerationRun:
        args = exercise_generation_claim_args(
            course_id="course:one",
            version_id="course_version:one",
            chapter_key="linear-equations",
            anchor_ids=[self.anchor_id],
            generation_model=GENERATION_MODEL,
            review_model=REVIEW_MODEL,
            prompt_version="v2",
        )
        input_hash = generation_input_hash(
            course_id="course:one",
            stage="exercise_bank",
            command_args=args,
            model=GENERATION_MODEL,
            prompt_version="v2",
            anchor_ids=[self.anchor_id],
            source_hashes={"source:one": SOURCE_HASH},
            course_version_id="course_version:one",
            chapter_id="chapter:one",
            chapter_key="linear-equations",
        )
        await self.repository.repo_query(
            """
            CREATE ONLY $run_id CONTENT {
                course: course:one,
                course_version: course_version:one,
                chapter: chapter:one,
                chapter_key: 'linear-equations',
                stage: 'exercise_bank',
                adapter: 'ollama',
                model: 'qwen3.5:9b',
                reasoning_effort: NONE,
                status: 'queued',
                prompt_version: 'v2',
                input_hash: $input_hash,
                output_hash: NONE,
                command: NONE,
                error_message: NONE
            };
            """,
            {
                "run_id": self.repository.ensure_record_id(run_id),
                "input_hash": input_hash,
            },
        )
        return CourseGenerationRun(
            id=run_id,
            course="course:one",
            course_version="course_version:one",
            chapter="chapter:one",
            chapter_key="linear-equations",
            stage="exercise_bank",
            adapter="ollama",
            model="qwen3.5:9b",
            status="queued",
            prompt_version="v2",
            input_hash=input_hash,
            command=None,
        )

    async def generate(
        self, *, run_id: str, command_id: str
    ) -> tuple[CourseExercise, ...]:
        return await self.service.generate_and_persist(
            run_id=run_id,
            command_id=command_id,
            course_id="course:one",
            version_id="course_version:one",
            chapter_key="linear-equations",
            anchor_ids=[self.anchor_id],
            generation_model=GENERATION_MODEL,
            review_model=REVIEW_MODEL,
            prompt_version="v2",
        )


@pytest_asyncio.fixture
async def workflow_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> WorkflowHarness:
    import open_notebook.course.assessment_service as assessment_module
    import open_notebook.course.exercise_workflow_service as exercise_module
    import open_notebook.course.models as models_module
    import open_notebook.course.workflow_service as workflow_module
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("exercise_workflow", "exercise_workflow")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    monkeypatch.setattr(models_module, "repo_query", repository.repo_query)
    monkeypatch.setattr(workflow_module, "repo_query", repository.repo_query)
    monkeypatch.setattr(assessment_module, "repo_query", repository.repo_query)
    monkeypatch.setattr(exercise_module, "repo_query", repository.repo_query)
    await database.query(
        "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
    )
    for version in ("24", "25", "26", "27"):
        await database.query(_migration(version))

    outline = {
        "title": "Algebra",
        "chapters": [
            {
                "key": "linear-equations",
                "title": "Linear equations",
                "purpose": "Solve linear equations.",
                "objective_keys": ["linear-equations"],
                "anchor_ids": [],
                "lab_keys": ["linear-plot"],
            }
        ],
        "concepts": [
            {
                "key": "linear-equations",
                "label": "Linear equations",
                "anchor_ids": [],
            }
        ],
        "dependency_edges": [],
    }
    evidence = EvidenceService(data_root=Path("/tmp/course-evidence"))
    anchor = evidence.make_anchor(
        course_id="course:one",
        source_id="source:one",
        source_sha256=SOURCE_HASH,
        kind="pdf_page",
        index=3,
        block_key="exercise-3-2",
        quote="Exercise 3.2. Solve 2x + 3 = 11.",
        source_role="PRIMARY",
    )
    outline["chapters"][0]["anchor_ids"] = [anchor.anchor_id]
    outline["concepts"][0]["anchor_ids"] = [anchor.anchor_id]
    chapter_artifact = {
        "chapter_key": "linear-equations",
        "content": "Current generated chapter",
    }
    chapter_run = CourseGenerationRun(
        id="course_generation_run:chapter",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="linear-equations",
        stage="chapter_content",
        adapter="ollama",
        model="qwen3.5:9b",
        status="succeeded",
        prompt_version="v1",
        input_hash="c" * 64,
        output_hash=_hash({"output": chapter_artifact}),
    )
    await repository.repo_query(
        """
        CREATE notebook:one SET name = 'Notebook';
        CREATE source:one SET title = 'Source';
        CREATE course:one SET
            title = 'Algebra', notebook = notebook:one, subject = 'math',
            status = 'generating', language = 'en', source_ids = [source:one],
            primary_source_ids = [source:one], supplement_source_ids = [],
            outline_version_id = course_version:one, outline = $outline;
        CREATE course_version:one SET
            course = course:one, version_no = 1, status = 'generating',
            outline_artifact = $outline, outline_hash = $outline_hash,
            approved_at = time::from::unix(1787932800), confirmation = '确认大纲';
        CREATE chapter:one SET
            course_version = course_version:one, chapter_no = 1,
            chapter_key = 'linear-equations', version_no = 1,
            title = 'Linear equations', status = 'ready',
            review_status = 'passed', validation_status = 'passed',
            artifact = $chapter_artifact, input_hash = $chapter_input_hash;
        CREATE course_generation_run:chapter CONTENT $chapter_run;
        CREATE course_evidence_anchor:one CONTENT $anchor;
        """,
        {
            "outline": outline,
            "outline_hash": _hash(outline),
            "chapter_artifact": chapter_artifact,
            "chapter_input_hash": artifact_replay_hash(chapter_run),
            "chapter_run": {
                **chapter_run.model_dump(mode="json", exclude={"id", "created", "updated"}),
                "course": repository.ensure_record_id("course:one"),
                "course_version": repository.ensure_record_id("course_version:one"),
                "chapter": repository.ensure_record_id("chapter:one"),
            },
            "anchor": {
                **anchor.model_dump(mode="json", exclude={"id", "created", "updated"}),
                "course": repository.ensure_record_id("course:one"),
                "source": repository.ensure_record_id("source:one"),
            },
        },
    )

    adapter = SequenceAdapter(
        [
            ExerciseBankArtifact(
                exercises=[_source(anchor.anchor_id), _core(anchor.anchor_id)]
            ),
            ReviewArtifact(findings=[]),
        ]
    )
    generation = CourseGenerationService(adapter)
    workflow = CourseWorkflowService(generation=generation, evidence=evidence)
    monkeypatch.setattr(workflow, "_source_hash", AsyncMock(return_value=SOURCE_HASH))
    service = ExerciseWorkflowService(workflow=workflow)
    harness = WorkflowHarness(
        database=database,
        service=service,
        adapter=adapter,
        anchor_id=anchor.anchor_id,
        repository=repository,
    )
    yield harness
    await database.close()


@pytest.mark.asyncio
async def test_exercise_bank_persists_atomically_and_replays_without_duplicates(
    workflow_harness: WorkflowHarness,
) -> None:
    await workflow_harness.create_parent_run(
        run_id="course_generation_run:parent", command_id="command:one"
    )

    first = await workflow_harness.generate(
        run_id="course_generation_run:parent", command_id="command:one"
    )
    second = await workflow_harness.generate(
        run_id="course_generation_run:parent", command_id="command:one"
    )

    assert tuple(str(item.id) for item in first) == tuple(
        str(item.id) for item in second
    )
    assert {str(item.id) for item in first} == {
        exercise_record_id(
            "course_version:one", "linear-equations", "linear-source"
        ),
        exercise_record_id(
            "course_version:one", "linear-equations", "linear-core"
        ),
    }
    assert len(workflow_harness.adapter.calls) == 2
    rows = await workflow_harness.repository.repo_query(
        "SELECT * FROM course_exercise WHERE course_version = course_version:one "
        "AND chapter_key = 'linear-equations' ORDER BY exercise_key;"
    )
    assert len(rows) == 2
    core = next(item for item in first if item.is_core)
    assert core.verification.level == "L1"
    assert core.verification.method == "independent_model_review"
    assert len(core.review_run_ids) == 1
    parent = await CourseGenerationRun.get("course_generation_run:parent")
    CourseWorkflowService.verify_completed_output(
        parent, canonical_exercise_output(first)
    )


@pytest.mark.asyncio
async def test_second_create_failure_rolls_back_the_entire_chapter_bank(
    workflow_harness: WorkflowHarness,
) -> None:
    await workflow_harness.create_parent_run(
        run_id="course_generation_run:first", command_id="command:first"
    )
    original = await workflow_harness.generate(
        run_id="course_generation_run:first", command_id="command:first"
    )
    original_keys = {item.exercise_key for item in original}

    new_source = _source(workflow_harness.anchor_id).model_copy(
        update={"key": "linear-source-v2"}
    )
    new_core = _core(workflow_harness.anchor_id).model_copy(
        update={"key": "linear-core-v2"}
    )
    workflow_harness.adapter.outputs.extend(
        [
            ExerciseBankArtifact(exercises=[new_source, new_core]),
            ReviewArtifact(findings=[]),
        ]
    )
    await workflow_harness.create_parent_run(
        run_id="course_generation_run:second", command_id="command:second"
    )
    collision_id = exercise_record_id(
        "course_version:one", "linear-equations", "linear-core-v2"
    )
    await workflow_harness.repository.repo_query(
        """
        CREATE ONLY $collision_id SET
            course = course:one, course_version = course_version:one,
            chapter = chapter:one, chapter_key = 'outside-scope',
            exercise_key = 'collision', blueprint = {},
            source_anchor_ids = [], difficulty = {}, grader = {},
            is_core = false, is_gating = false, is_source_level = false,
            verification = { level: 'L1', method: 'self_consistency', anchor_ids: [] },
            generation_run = NONE, review_run_ids = [];
        """,
        {"collision_id": workflow_harness.repository.ensure_record_id(collision_id)},
    )

    with pytest.raises(RuntimeError):
        await workflow_harness.generate(
            run_id="course_generation_run:second", command_id="command:second"
        )

    remaining = await workflow_harness.repository.repo_query(
        "SELECT exercise_key FROM course_exercise "
        "WHERE course_version = course_version:one "
        "AND chapter_key = 'linear-equations';"
    )
    assert {row["exercise_key"] for row in remaining} == original_keys


@pytest.mark.asyncio
async def test_committed_transaction_is_reconciled_after_ambiguous_disconnect(
    workflow_harness: WorkflowHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import open_notebook.course.exercise_workflow_service as exercise_module

    await workflow_harness.create_parent_run(
        run_id="course_generation_run:parent", command_id="command:one"
    )
    repository_query = workflow_harness.repository.repo_query

    async def commit_then_disconnect(statement: str, variables=None):
        result = await repository_query(statement, variables)
        if "BEGIN TRANSACTION" in statement and "DELETE course_exercise" in statement:
            raise RuntimeError("connection closed after commit")
        return result

    monkeypatch.setattr(exercise_module, "repo_query", commit_then_disconnect)

    exercises = await workflow_harness.generate(
        run_id="course_generation_run:parent", command_id="command:one"
    )

    assert {item.exercise_key for item in exercises} == {
        "linear-source",
        "linear-core",
    }
    run = await CourseGenerationRun.get("course_generation_run:parent")
    assert run.status == "succeeded"
    CourseWorkflowService.verify_completed_output(
        run, canonical_exercise_output(exercises)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["published", "outline_hash", "old_chapter"])
async def test_immutable_or_stale_inputs_fail_before_any_model_call(
    workflow_harness: WorkflowHarness,
    mutation: str,
) -> None:
    await workflow_harness.create_parent_run(
        run_id="course_generation_run:blocked", command_id="command:blocked"
    )
    if mutation == "published":
        await workflow_harness.repository.repo_query(
            "UPDATE course_version:one SET status = 'published';"
        )
    elif mutation == "outline_hash":
        await workflow_harness.repository.repo_query(
            "UPDATE course_version:one SET outline_hash = $hash;",
            {"hash": "0" * 64},
        )
    else:
        await workflow_harness.repository.repo_query(
            """
            CREATE chapter:new SET
                course_version = course_version:one, chapter_no = 1,
                chapter_key = 'linear-equations', version_no = 2,
                title = 'New current chapter', status = 'ready', input_hash = NONE;
            """
        )

    with pytest.raises(ValueError):
        await workflow_harness.generate(
            run_id="course_generation_run:blocked", command_id="command:blocked"
        )

    assert workflow_harness.adapter.calls == []
