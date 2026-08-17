"""Course worker orchestration contracts and registration."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


class _FakeQueueStore:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, object]] = {}
        self.commands: dict[str, dict[str, object]] = {}
        self.submit_count = 0

    async def query(self, statement: str, variables: dict[str, object] | None = None):
        variables = variables or {}
        if "FROM course_generation_run WHERE input_hash" in statement:
            rows = [
                dict(row)
                for row in self.runs.values()
                if row["input_hash"] == variables["input_hash"]
            ]
            return sorted(rows, key=lambda row: str(row["id"]), reverse=True)
        if statement.lstrip().startswith("CREATE ONLY $run_id"):
            run_id = str(variables["run_id"])
            if run_id in self.runs:
                raise RuntimeError("record already exists")
            row = {"id": run_id, **dict(variables["payload"])}
            self.runs[run_id] = row
            return [dict(row)]
        if "FROM command WHERE" in statement and "args.run_id" in statement:
            rows = [
                dict(row)
                for row in self.commands.values()
                if dict(row["args"])["run_id"] == variables["run_id"]
            ]
            return rows[-1:]
        if statement.lstrip().startswith("UPDATE $run_id SET command"):
            run_id = str(variables["run_id"])
            self.runs[run_id]["command"] = str(variables["command_id"])
            return [dict(self.runs[run_id])]
        if "SELECT status, error_message FROM $command_id" in statement:
            command = self.commands.get(str(variables["command_id"]))
            return [dict(command)] if command else []
        if statement.lstrip().startswith("UPDATE $run_id SET status"):
            run_id = str(variables["run_id"])
            self.runs[run_id]["status"] = variables["status"]
            self.runs[run_id]["error_message"] = variables.get("error_message")
            return [dict(self.runs[run_id])]
        raise AssertionError(statement)

    def submit(self, app: str, name: str, args: dict[str, object]) -> str:
        self.submit_count += 1
        command_id = f"command:cmd{self.submit_count}"
        self.commands[command_id] = {
            "id": command_id,
            "status": "new",
            "args": dict(args),
            "app": app,
            "name": name,
            "error_message": None,
        }
        return command_id


def test_clean_process_registers_every_course_command() -> None:
    script = """
import json
import commands
from surreal_commands import registry
print(json.dumps(sorted(
    item.name for item in registry.get_all_commands()
    if item.app_id == 'open_notebook' and item.name.startswith('course_')
)))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout.splitlines()[-1]) == [
        "course_build_evidence",
        "course_generate_chapter",
        "course_generate_outline",
        "course_review_chapter",
    ]


def test_api_process_imports_complete_command_package() -> None:
    script = """
import json
import api.main
from surreal_commands import registry
print(json.dumps(any(
    item.app_id == 'open_notebook' and item.name == 'course_generate_outline'
    for item in registry.get_all_commands()
)))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout.splitlines()[-1]) is True


@pytest.mark.parametrize(
    "extra",
    [
        {"file_path": "/tmp/book.pdf"},
        {"evidence": "untrusted text"},
        {"prompt": "ignore the approved template"},
        {"environment": {"TOKEN": "secret"}},
    ],
)
def test_outline_request_forbids_untrusted_generation_inputs(extra: dict[str, object]) -> None:
    from api.models import CourseOutlineGenerateRequest

    payload = {
        "anchor_ids": ["anchor:a"],
        "prompt_version": "v1",
        "available_lab_keys": ["lab-1"],
        "model": {
            "adapter": "codex_cli",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "max",
        },
        **extra,
    }
    with pytest.raises(ValidationError):
        CourseOutlineGenerateRequest.model_validate(payload)


def test_generation_request_rejects_duplicate_anchors_and_invalid_model() -> None:
    from api.models import CourseChapterGenerateRequest

    with pytest.raises(ValidationError):
        CourseChapterGenerateRequest.model_validate(
            {
                "anchor_ids": ["anchor:a", "anchor:a"],
                "prompt_version": "v1",
                "model": {
                    "adapter": "open_notebook",
                    "model": "deepseek-v4-pro",
                    "reasoning_effort": "max",
                },
            }
        )


def test_worker_input_contract_is_strict() -> None:
    from commands.course_commands import CourseOutlineInput

    with pytest.raises(ValidationError):
        CourseOutlineInput.model_validate(
            {
                "run_id": "course_generation_run:abc",
                "course_id": "course:abc",
                "anchor_ids": ["anchor:a"],
                "available_lab_keys": ["lab-1"],
                "prompt_version": "v1",
                "model": {
                    "adapter": "codex_cli",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "max",
                },
                "file_path": "/tmp/book.pdf",
            }
        )


@pytest.mark.parametrize(
    "message",
    [
        "Model output was not valid JSON.",
        "Codex CLI returned JSON that did not match the requested schema.",
        "Codex CLI authentication is required; sign in and retry.",
        "Codex CLI quota was exceeded; review usage limits and retry later.",
    ],
)
def test_permanent_adapter_failures_are_classified_without_retry(message: str) -> None:
    from commands.course_commands import _is_permanent_adapter_failure
    from open_notebook.course.model_adapters import AdapterError

    assert _is_permanent_adapter_failure(AdapterError(message)) is True
    assert (
        _is_permanent_adapter_failure(
            AdapterError("Codex CLI timed out after 1800 seconds.")
        )
        is False
    )


@pytest.mark.asyncio
async def test_persistent_active_run_dedupe_and_ordered_key(monkeypatch) -> None:
    import api.course_command_service as module

    store = _FakeQueueStore()
    monkeypatch.setattr(module, "repo_query", store.query)
    monkeypatch.setattr(module, "submit_command", store.submit)
    service = module.CourseCommandService()
    common = {
        "course_id": "course:abc",
        "stage": "outline",
        "command_name": "course_generate_outline",
        "command_args": {"course_id": "course:abc"},
        "model": {
            "adapter": "codex_cli",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "max",
        },
        "prompt_version": "v1",
        "source_hashes": {"source:a": "a" * 64},
    }
    first = await service.submit_stage(anchor_ids=["anchor:a", "anchor:b"], **common)
    # A new service simulates API-process restart; persistence, not a memory cache,
    # must still find the active run.
    second = await module.CourseCommandService().submit_stage(
        anchor_ids=["anchor:a", "anchor:b"], **common
    )
    reordered = await service.submit_stage(
        anchor_ids=["anchor:b", "anchor:a"], **common
    )

    assert second == first
    assert reordered.run_id != first.run_id
    assert store.submit_count == 2


@pytest.mark.asyncio
async def test_concurrent_claim_terminal_retry_and_force(monkeypatch) -> None:
    import api.course_command_service as module

    store = _FakeQueueStore()
    monkeypatch.setattr(module, "repo_query", store.query)
    monkeypatch.setattr(module, "submit_command", store.submit)
    service = module.CourseCommandService()
    kwargs = {
        "course_id": "course:abc",
        "stage": "review",
        "command_name": "course_review_chapter",
        "command_args": {"course_id": "course:abc", "chapter_key": "one"},
        "model": {
            "adapter": "codex_cli",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
        },
        "prompt_version": "v1",
        "anchor_ids": ["anchor:a"],
        "source_hashes": {"source:a": "a" * 64},
        "chapter_key": "one",
    }
    one, two = await asyncio.gather(
        service.submit_stage(**kwargs), service.submit_stage(**kwargs)
    )
    assert one == two
    assert store.submit_count == 1

    store.runs[one.run_id]["status"] = "failed"
    store.commands[one.command_id]["status"] = "failed"
    retried = await service.submit_stage(**kwargs)
    forced = await service.submit_stage(**kwargs, force=True)
    assert retried.run_id != one.run_id
    assert forced.run_id not in {one.run_id, retried.run_id}
    assert store.submit_count == 3


@pytest.mark.asyncio
async def test_unbound_run_recovers_submitted_command_after_crash(monkeypatch) -> None:
    import api.course_command_service as module

    store = _FakeQueueStore()
    monkeypatch.setattr(module, "repo_query", store.query)
    monkeypatch.setattr(module, "submit_command", store.submit)
    service = module.CourseCommandService()
    kwargs = {
        "course_id": "course:abc",
        "stage": "evidence",
        "command_name": "course_build_evidence",
        "command_args": {"course_id": "course:abc", "source_id": "source:a"},
        "model": {"adapter": "open_notebook", "model": "docling"},
        "prompt_version": "evidence-v1",
        "anchor_ids": [],
        "source_hashes": {"source:a": "a" * 64},
    }
    original_query = store.query
    fail_binding_once = True

    async def flaky_query(statement, variables=None):
        nonlocal fail_binding_once
        if statement.lstrip().startswith("UPDATE $run_id SET command") and fail_binding_once:
            fail_binding_once = False
            raise RuntimeError("crash after command creation")
        return await original_query(statement, variables)

    monkeypatch.setattr(module, "repo_query", flaky_query)
    with pytest.raises(RuntimeError, match="crash after command creation"):
        await service.submit_stage(**kwargs)

    recovered = await module.CourseCommandService().submit_stage(**kwargs)
    assert recovered.command_id == "command:cmd1"
    assert store.submit_count == 1


@pytest.mark.asyncio
async def test_course_lock_is_reentrant_for_worker_and_ollama_layers() -> None:
    from open_notebook.course.locking import course_job_lock

    async def nested() -> bool:
        async with course_job_lock():
            async with course_job_lock():
                return True

    assert await asyncio.wait_for(nested(), timeout=0.2)


def test_outline_facade_returns_202_job(monkeypatch) -> None:
    from api.course_command_service import CourseCommandService, CourseJobSubmission
    from api.main import app

    monkeypatch.setattr(
        CourseCommandService,
        "submit_outline",
        AsyncMock(
            return_value=CourseJobSubmission(
                command_id="command:abc",
                run_id="course_generation_run:abc",
                status="queued",
            )
        ),
        raising=False,
    )
    response = TestClient(app).post(
        "/api/courses/course:abc/outline/generate",
        json={
            "anchor_ids": ["anchor:a"],
            "available_lab_keys": ["lab-1"],
            "prompt_version": "v1",
            "model": {
                "adapter": "codex_cli",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "max",
            },
        },
    )
    assert response.status_code == 202
    assert response.json() == {
        "command_id": "command:abc",
        "run_id": "course_generation_run:abc",
        "status": "queued",
    }


def test_retrieval_facade_returns_evidence_only(monkeypatch) -> None:
    from api.course_command_service import CourseCommandService
    from api.main import app

    monkeypatch.setattr(
        CourseCommandService,
        "retrieval_context",
        AsyncMock(
            return_value={
                "course_id": "course:abc",
                "anchor_ids": ["anchor:a"],
                "context": ["PRIMARY pdf_page 1 [anchor:a]: grounded"],
            }
        ),
        raising=False,
    )
    response = TestClient(app).post(
        "/api/courses/course:abc/retrieval/context",
        json={"anchor_ids": ["anchor:a"]},
    )
    assert response.status_code == 200
    assert set(response.json()) == {"course_id", "anchor_ids", "context"}


@pytest.mark.asyncio
async def test_worker_grounding_preserves_order_and_rejects_tampering(monkeypatch) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.evidence_service import (
        EvidenceInputError,
        EvidenceService,
    )
    from open_notebook.course.models import Course

    course = Course(
        id="course:abc",
        title="Physics",
        notebook="notebook:abc",
        source_ids=["source:abc"],
        primary_source_ids=["source:abc"],
    )
    evidence = EvidenceService()
    first = evidence.make_anchor(
        course_id="course:abc",
        source_id="source:abc",
        source_sha256="a" * 64,
        kind="pdf_page",
        index=1,
        block_key="one",
        quote="first",
        source_role="PRIMARY",
    )
    second = evidence.make_anchor(
        course_id="course:abc",
        source_id="source:abc",
        source_sha256="a" * 64,
        kind="pdf_page",
        index=2,
        block_key="two",
        quote="second",
        source_role="PRIMARY",
    )

    async def anchors_query(statement, variables=None):
        del statement, variables
        return [first.model_dump(mode="json"), second.model_dump(mode="json")]

    monkeypatch.setattr(module, "repo_query", anchors_query)
    workflow = module.CourseWorkflowService()
    monkeypatch.setattr(workflow, "_source_hash", AsyncMock(return_value="a" * 64))
    _, _, context = await workflow.grounded_inputs(
        course=course, anchor_ids=[second.anchor_id, first.anchor_id]
    )
    assert "second" in context[0]
    assert "first" in context[1]

    broken = second.model_dump(mode="json")
    broken["locator"]["quote"] = "tampered"

    async def tampered_query(statement, variables=None):
        del statement, variables
        return [first.model_dump(mode="json"), broken]

    monkeypatch.setattr(module, "repo_query", tampered_query)
    with pytest.raises(EvidenceInputError, match="quote hash mismatch"):
        await workflow.grounded_inputs(course=course, anchor_ids=[second.anchor_id])


@pytest.mark.asyncio
async def test_exact_approval_gate_precedes_chapter_model_call(monkeypatch) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.models import Course, CourseGenerationRun, CourseVersion

    generation = AsyncMock()
    workflow = module.CourseWorkflowService(generation=generation)
    course = Course(
        id="course:abc",
        title="Physics",
        notebook="notebook:abc",
        status="outline_approved",
        outline_version_id="course_version:one",
    )
    version = CourseVersion(
        id="course_version:one",
        course="course:abc",
        version_no=1,
        outline_artifact={"title": "x", "chapters": []},
        confirmation="确认大纲",
        approved_at=None,
    )
    run = CourseGenerationRun(
        id="course_generation_run:one",
        course="course:abc",
        course_version="course_version:one",
        chapter_key="one",
        stage="chapter_content",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        reasoning_effort="max",
        prompt_version="v1",
        input_hash="a" * 64,
    )
    monkeypatch.setattr(module.Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(module.CourseVersion, "get", AsyncMock(return_value=version))

    with pytest.raises(ValueError, match="not approved"):
        await workflow.generate_chapter(
            run=run,
            course_id="course:abc",
            chapter_key="one",
            anchor_ids=["anchor:a"],
            model=module.ModelSelection(
                adapter="codex_cli",
                model="gpt-5.6-sol",
                reasoning_effort="max",
            ),
            prompt_version="v1",
        )
    generation.generate_chapter.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_rejects_wrong_command_binding(monkeypatch) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.models import CourseGenerationRun

    run = CourseGenerationRun(
        id="course_generation_run:one",
        course="course:abc",
        stage="outline",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        reasoning_effort="max",
        prompt_version="v1",
        input_hash="a" * 64,
        command="command:right",
    )
    monkeypatch.setattr(
        module.CourseGenerationRun, "get", AsyncMock(return_value=run)
    )
    with pytest.raises(ValueError, match="binding mismatch"):
        await module.CourseWorkflowService().load_run(
            run_id=str(run.id),
            course_id="course:abc",
            stage="outline",
            command_id="command:wrong",
        )


@pytest.mark.asyncio
async def test_generic_command_status_synchronizes_course_run(monkeypatch) -> None:
    import api.command_service as module

    monkeypatch.setattr(
        module,
        "get_command_status",
        AsyncMock(
            return_value=SimpleNamespace(
                status="failed",
                result=None,
                error_message="provider timeout",
                created=None,
                updated=None,
                progress=None,
            )
        ),
    )
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(module, "repo_query", query, raising=False)

    result = await module.CommandService.get_command_status("command:abc")

    assert result["status"] == "failed"
    assert "status = $run_status" in query.await_args.args[0]
    assert query.await_args.args[1]["run_status"] == "failed"


@pytest.mark.asyncio
async def test_fake_adapter_outline_approval_chapter_review_publish_replays_once(
    monkeypatch,
) -> None:
    """DB-free end-to-end proof of the V2 worker artifact path."""

    import open_notebook.course.workflow_service as module
    from api.course_service import CourseService
    from open_notebook.course.contracts import (
        ChapterArtifact,
        ChapterSection,
        CourseOutlineArtifact,
        ExerciseArtifact,
        FormulaArtifact,
        FunctionPlotLabSpec,
        ModelSelection,
        ReviewArtifact,
        WorkedExampleArtifact,
    )
    from open_notebook.course.evidence_service import EvidenceService
    from open_notebook.course.generation_service import CourseGenerationService
    from open_notebook.course.model_adapters import FakeCourseModelAdapter
    from open_notebook.course.models import (
        Chapter,
        Course,
        CourseGenerationRun,
        CourseVersion,
        Lab,
    )

    source_hash = "a" * 64
    anchor = EvidenceService().make_anchor(
        course_id="course:e2e",
        source_id="source:e2e",
        source_sha256=source_hash,
        kind="pdf_page",
        index=1,
        block_key="block-1",
        quote="Grounded fact.",
        source_role="PRIMARY",
    )
    course = Course(
        id="course:e2e",
        title="Calculus",
        notebook="notebook:e2e",
        source_ids=["source:e2e"],
        primary_source_ids=["source:e2e"],
    )
    outline = CourseOutlineArtifact(
        title="Calculus",
        chapters=[
            {
                "key": "limits",
                "title": "Limits",
                "purpose": "Learn limits.",
                "objective_keys": ["limit"],
                "anchor_ids": [anchor.anchor_id],
                "lab_keys": ["limit-plot"],
            }
        ],
        concepts=[
            {
                "key": "limit",
                "label": "Limit",
                "anchor_ids": [anchor.anchor_id],
            }
        ],
    )
    chapter_artifact = ChapterArtifact(
        chapter_key="limits",
        purpose="Learn limits.",
        prerequisites=["algebra"],
        objectives=["Evaluate limits"],
        sections=[
            ChapterSection(
                key="definition",
                title="Definition",
                markdown="Grounded definition.",
                anchor_ids=[anchor.anchor_id],
            )
        ],
        definitions=["Limit"],
        formulas=[
            FormulaArtifact(
                key="identity",
                latex="x",
                meaning="Identity",
                anchor_ids=[anchor.anchor_id],
                oracle_expression="x",
            )
        ],
        worked_examples=[
            WorkedExampleArtifact(
                key="example",
                prompt="Compute 2 + 2.",
                steps=["Add."],
                answer="4",
                anchor_ids=[anchor.anchor_id],
                oracle_expression="2 + 2",
                oracle_answer=4,
            )
        ],
        labs=[
            FunctionPlotLabSpec(
                key="limit-plot",
                title="Plot",
                expressions=["x"],
            )
        ],
        pitfalls=["Check the domain."],
        exercises=[
            ExerciseArtifact(
                key="core",
                prompt="Evaluate.",
                difficulty="core",
                hints=["h1", "h2", "h3", "h4"],
                answer="2",
                transfer_task="Try another.",
                anchor_ids=[anchor.anchor_id],
            )
        ],
        quick_reference=["lim"],
        citations=[anchor.anchor_id],
    )
    versions: list[CourseVersion] = []
    chapters: list[Chapter] = []
    labs: list[Lab] = []

    async def save_course(self):
        return None

    async def save_version(self):
        if self.id is None:
            self.id = f"course_version:v{len(versions) + 1}"
            versions.append(self)

    async def save_chapter(self):
        if self.id is None:
            self.id = f"chapter:c{len(chapters) + 1}"
            chapters.append(self)

    async def save_lab(self):
        if self.id is None:
            self.id = f"lab:l{len(labs) + 1}"
            labs.append(self)

    async def save_run(self):
        return None

    async def get_course(_course_id):
        return course

    async def get_version(version_id):
        return next(item for item in versions if item.id == version_id)

    async def get_chapter(chapter_id):
        return next(item for item in chapters if item.id == chapter_id)

    async def list_versions(_course_id):
        return versions

    async def list_chapters(_version_id):
        return chapters

    async def query(statement, variables=None):
        variables = variables or {}
        if "FROM course_evidence_anchor" in statement:
            return [anchor.model_dump(mode="json")]
        if "FROM course_version WHERE" in statement:
            return [
                item.model_dump(mode="json")
                for item in versions
                if item.input_hash == variables["hash"]
            ]
        if "FROM chapter WHERE course_version" in statement:
            return [
                item.model_dump(mode="json")
                for item in chapters
                if item.input_hash == variables["hash"]
            ]
        if "SELECT * FROM lab WHERE" in statement:
            return [item.model_dump(mode="json") for item in labs]
        if "course_validation_finding" in statement:
            return []
        if statement.lstrip().startswith("UPDATE"):
            return []
        raise AssertionError(statement)

    monkeypatch.setattr(module, "repo_query", query)
    monkeypatch.setattr(module.Course, "get", get_course)
    monkeypatch.setattr(module.CourseVersion, "get", get_version)
    monkeypatch.setattr(module.Chapter, "get", get_chapter)
    monkeypatch.setattr(module.Course, "versions", list_versions)
    monkeypatch.setattr(module.CourseVersion, "chapters", list_chapters)
    monkeypatch.setattr(module.Course, "save", save_course)
    monkeypatch.setattr(module.CourseVersion, "save", save_version)
    monkeypatch.setattr(module.Chapter, "save", save_chapter)
    monkeypatch.setattr(module.Lab, "save", save_lab)
    monkeypatch.setattr(module.CourseGenerationRun, "save", save_run)
    monkeypatch.setattr(CourseService, "get_course", AsyncMock(return_value=course))

    adapter = FakeCourseModelAdapter(outline)
    workflow = module.CourseWorkflowService(
        generation=CourseGenerationService(adapter=adapter)
    )
    monkeypatch.setattr(workflow, "_source_hash", AsyncMock(return_value=source_hash))
    monkeypatch.setattr(
        workflow.evidence, "build", AsyncMock(return_value=[anchor])
    )
    selection = ModelSelection(
        adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
    )
    evidence_run = CourseGenerationRun(
        id="course_generation_run:evidence",
        course="course:e2e",
        stage="evidence",
        adapter="open_notebook",
        model="docling",
        status="running",
        prompt_version="evidence-v1",
        input_hash="0" * 64,
    )
    built = await workflow.build_evidence(
        run=evidence_run,
        course_id="course:e2e",
        source_id="source:e2e",
        role="PRIMARY",
    )
    assert [item.anchor_id for item in built] == [anchor.anchor_id]
    assert course.status == "indexing"
    outline_run = CourseGenerationRun(
        id="course_generation_run:outline",
        course="course:e2e",
        stage="outline",
        adapter=selection.adapter,
        model=selection.model,
        reasoning_effort=selection.reasoning_effort,
        status="running",
        prompt_version="v1",
        input_hash="1" * 64,
    )
    version = await workflow.generate_outline(
        run=outline_run,
        course_id="course:e2e",
        anchor_ids=[anchor.anchor_id],
        available_lab_keys=["limit-plot"],
        model=selection,
        prompt_version="v1",
    )
    await workflow.generate_outline(
        run=outline_run,
        course_id="course:e2e",
        anchor_ids=[anchor.anchor_id],
        available_lab_keys=["limit-plot"],
        model=selection,
        prompt_version="v1",
    )
    assert len(adapter.calls) == 1

    await CourseService.approve_outline(
        "course:e2e", str(version.id), "确认大纲"
    )
    adapter.output = chapter_artifact
    chapter_run = CourseGenerationRun(
        id="course_generation_run:chapter",
        course="course:e2e",
        course_version=str(version.id),
        chapter_key="limits",
        stage="chapter_content",
        adapter=selection.adapter,
        model=selection.model,
        reasoning_effort=selection.reasoning_effort,
        status="running",
        prompt_version="v1",
        input_hash="2" * 64,
    )
    chapter = await workflow.generate_chapter(
        run=chapter_run,
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=selection,
        prompt_version="v1",
    )
    await workflow.generate_chapter(
        run=chapter_run,
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=selection,
        prompt_version="v1",
    )
    assert len(adapter.calls) == 2
    assert len(chapters) == 1
    assert len(labs) == 1

    adapter.output = ReviewArtifact(findings=[])
    review_selection = ModelSelection(
        adapter="codex_cli", model="gpt-5.6-luna", reasoning_effort="max"
    )
    review_run = CourseGenerationRun(
        id="course_generation_run:review",
        course="course:e2e",
        course_version=str(version.id),
        chapter=str(chapter.id),
        chapter_key="limits",
        stage="review",
        adapter=review_selection.adapter,
        model=review_selection.model,
        reasoning_effort=review_selection.reasoning_effort,
        status="running",
        prompt_version="v1",
        input_hash="3" * 64,
    )
    reviewed, findings = await workflow.review_chapter(
        run=review_run,
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=review_selection,
        prompt_version="v1",
    )
    await workflow.review_chapter(
        run=review_run,
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=review_selection,
        prompt_version="v1",
    )
    assert findings == []
    assert len(adapter.calls) == 3
    assert reviewed.status == "ready"

    published = await CourseService.publish_chapter(
        "course:e2e", str(version.id), str(chapter.id)
    )
    assert published.status == "published"
