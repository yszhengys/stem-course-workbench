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


def test_worker_recomputes_claim_hash_and_rejects_reordered_queue_anchors() -> None:
    import hashlib

    from open_notebook.course.contracts import ModelSelection
    from open_notebook.course.models import CourseGenerationRun
    from open_notebook.course.workflow_service import CourseWorkflowService

    selection = ModelSelection(
        adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
    )
    command_args = {
        "course_id": "course:abc",
        "anchor_ids": ["anchor:a", "anchor:b"],
        "available_lab_keys": ["lab-1"],
        "prompt_version": "v1",
        "model": selection.model_dump(mode="json"),
    }
    key = {
        "course_id": "course:abc",
        "stage": "outline",
        "course_version_id": None,
        "chapter_id": None,
        "chapter_key": None,
        "prompt_version": "v1",
        "model": selection.model_dump(mode="json"),
        "anchor_ids": ["anchor:a", "anchor:b"],
        "source_hashes": {"source:a": "a" * 64},
        "stable_args": command_args,
    }
    expected = hashlib.sha256(
        json.dumps(
            key,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    run = CourseGenerationRun(
        id="course_generation_run:one",
        course="course:abc",
        stage="outline",
        adapter=selection.adapter,
        model=selection.model,
        reasoning_effort=selection.reasoning_effort,
        prompt_version="v1",
        input_hash=expected,
    )
    CourseWorkflowService.validate_run_claim(
        run,
        command_args=command_args,
        model=selection,
        prompt_version="v1",
        anchor_ids=["anchor:a", "anchor:b"],
        source_hashes={"source:a": "a" * 64},
    )
    reordered_args = {
        **command_args,
        "anchor_ids": ["anchor:b", "anchor:a"],
    }
    with pytest.raises(ValueError, match="claim hash"):
        CourseWorkflowService.validate_run_claim(
            run,
            command_args=reordered_args,
            model=selection,
            prompt_version="v1",
            anchor_ids=["anchor:b", "anchor:a"],
            source_hashes={"source:a": "a" * 64},
        )


@pytest.mark.parametrize(
    "tampered_args",
    [
        {
            "course_id": "course:abc",
            "source_id": "source:other",
            "role": "PRIMARY",
        },
        {
            "course_id": "course:abc",
            "source_id": "source:abc",
            "role": "SUPPLEMENT",
        },
    ],
)
def test_worker_claim_hash_rejects_source_or_role_tamper(
    tampered_args: dict[str, str],
) -> None:
    from open_notebook.course.contracts import ModelSelection
    from open_notebook.course.models import CourseGenerationRun
    from open_notebook.course.workflow_service import (
        CourseWorkflowService,
        generation_input_hash,
    )

    model = ModelSelection(adapter="open_notebook", model="docling")
    expected_args = {
        "course_id": "course:abc",
        "source_id": "source:abc",
        "role": "PRIMARY",
    }
    run = CourseGenerationRun(
        id="course_generation_run:evidence",
        course="course:abc",
        stage="evidence",
        adapter=model.adapter,
        model=model.model,
        prompt_version="evidence-v1",
        input_hash=generation_input_hash(
            course_id="course:abc",
            stage="evidence",
            command_args=expected_args,
            model=model,
            prompt_version="evidence-v1",
            anchor_ids=[],
            source_hashes={"source:abc": "a" * 64},
        ),
    )

    with pytest.raises(ValueError, match="claim hash"):
        CourseWorkflowService.validate_run_claim(
            run,
            command_args=tampered_args,
            model=model,
            prompt_version="evidence-v1",
            anchor_ids=[],
            source_hashes={"source:abc": "a" * 64},
        )


def test_worker_claim_hash_rejects_available_lab_key_tamper() -> None:
    from open_notebook.course.contracts import ModelSelection
    from open_notebook.course.models import CourseGenerationRun
    from open_notebook.course.workflow_service import (
        CourseWorkflowService,
        generation_input_hash,
    )

    model = ModelSelection(
        adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
    )
    expected_args = {
        "course_id": "course:abc",
        "anchor_ids": ["anchor:a"],
        "available_lab_keys": ["lab-1"],
        "prompt_version": "v1",
        "model": model.model_dump(mode="json"),
    }
    run = CourseGenerationRun(
        id="course_generation_run:outline",
        course="course:abc",
        stage="outline",
        adapter=model.adapter,
        model=model.model,
        reasoning_effort=model.reasoning_effort,
        prompt_version="v1",
        input_hash=generation_input_hash(
            course_id="course:abc",
            stage="outline",
            command_args=expected_args,
            model=model,
            prompt_version="v1",
            anchor_ids=["anchor:a"],
            source_hashes={"source:abc": "a" * 64},
        ),
    )

    with pytest.raises(ValueError, match="claim hash"):
        CourseWorkflowService.validate_run_claim(
            run,
            command_args={**expected_args, "available_lab_keys": ["lab-2"]},
            model=model,
            prompt_version="v1",
            anchor_ids=["anchor:a"],
            source_hashes={"source:abc": "a" * 64},
        )


def test_artifact_replay_identity_is_unique_per_run_but_stable_for_replay() -> None:
    from open_notebook.course.models import CourseGenerationRun
    from open_notebook.course.workflow_service import artifact_replay_hash

    def run(run_id: str) -> CourseGenerationRun:
        return CourseGenerationRun(
            id=run_id,
            course="course:abc",
            stage="outline",
            adapter="codex_cli",
            model="gpt-5.6-sol",
            reasoning_effort="max",
            prompt_version="v1",
            input_hash="a" * 64,
        )

    first = run("course_generation_run:first")
    second = run("course_generation_run:second")

    assert artifact_replay_hash(first) == artifact_replay_hash(first)
    assert artifact_replay_hash(first) != artifact_replay_hash(second)


def test_succeeded_replay_fails_closed_on_output_hash_mismatch() -> None:
    from open_notebook.course.models import CourseGenerationRun
    from open_notebook.course.workflow_service import CourseWorkflowService

    run = CourseGenerationRun(
        id="course_generation_run:one",
        course="course:abc",
        stage="outline",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        reasoning_effort="max",
        status="succeeded",
        prompt_version="v1",
        input_hash="a" * 64,
        output_hash="b" * 64,
        command="command:one",
    )

    with pytest.raises(ValueError, match="output hash mismatch"):
        CourseWorkflowService.verify_completed_output(run, {"title": "tampered"})


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
async def test_unbound_recovery_rejects_tampered_command_name_or_args(
    monkeypatch,
) -> None:
    import api.course_command_service as module

    store = _FakeQueueStore()
    monkeypatch.setattr(module, "repo_query", store.query)
    monkeypatch.setattr(module, "submit_command", store.submit)
    service = module.CourseCommandService()
    kwargs = {
        "course_id": "course:abc",
        "stage": "evidence",
        "command_name": "course_build_evidence",
        "command_args": {
            "course_id": "course:abc",
            "source_id": "source:a",
            "role": "PRIMARY",
        },
        "model": {"adapter": "open_notebook", "model": "docling"},
        "prompt_version": "evidence-v1",
        "anchor_ids": [],
        "source_hashes": {"source:a": "a" * 64},
    }
    original_query = store.query

    async def crash_before_binding(statement, variables=None):
        if statement.lstrip().startswith("UPDATE $run_id SET command"):
            raise RuntimeError("crash after command creation")
        return await original_query(statement, variables)

    monkeypatch.setattr(module, "repo_query", crash_before_binding)
    with pytest.raises(RuntimeError, match="crash after command creation"):
        await service.submit_stage(**kwargs)

    store.commands["command:cmd1"]["name"] = "course_generate_outline"
    dict(store.commands["command:cmd1"]["args"])["role"] = "SUPPLEMENT"
    tampered_args = dict(store.commands["command:cmd1"]["args"])
    tampered_args["role"] = "SUPPLEMENT"
    store.commands["command:cmd1"]["args"] = tampered_args
    monkeypatch.setattr(module, "repo_query", original_query)

    recovered = await module.CourseCommandService().submit_stage(**kwargs)

    assert recovered.command_id == "command:cmd2"
    assert store.submit_count == 2


@pytest.mark.asyncio
async def test_worker_atomically_claims_unbound_run_after_full_preflight(
    monkeypatch,
) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.contracts import ModelSelection
    from open_notebook.course.models import Course, CourseGenerationRun

    source_hash = "a" * 64
    model = ModelSelection(adapter="open_notebook", model="docling")
    command_args = {
        "course_id": "course:abc",
        "source_id": "source:abc",
        "role": "PRIMARY",
    }
    run = CourseGenerationRun(
        id="course_generation_run:one",
        course="course:abc",
        stage="evidence",
        adapter=model.adapter,
        model=model.model,
        status="queued",
        prompt_version="evidence-v1",
        input_hash=module.generation_input_hash(
            course_id="course:abc",
            stage="evidence",
            command_args=command_args,
            model=model,
            prompt_version="evidence-v1",
            anchor_ids=[],
            source_hashes={"source:abc": source_hash},
        ),
    )
    course = Course(
        id="course:abc",
        title="Physics",
        notebook="notebook:abc",
        source_ids=["source:abc"],
        primary_source_ids=["source:abc"],
    )
    workflow = module.CourseWorkflowService()
    monkeypatch.setattr(module.Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(workflow, "_source_hash", AsyncMock(return_value=source_hash))
    monkeypatch.setattr(module.Course, "save", AsyncMock(return_value=None))
    monkeypatch.setattr(module.CourseGenerationRun, "save", AsyncMock(return_value=None))

    async def bind_query(statement, variables=None):
        if "SET command = $command_id" not in statement:
            raise AssertionError(statement)
        assert run.command is None
        run.command = str(variables["command_id"])
        run.status = "running"
        return [run.model_dump(mode="json")]

    monkeypatch.setattr(module, "repo_query", bind_query)

    async def build_after_binding(**kwargs):
        del kwargs
        assert run.command == "command:cmd1"
        assert run.status == "running"
        return []

    monkeypatch.setattr(workflow.evidence, "build", build_after_binding)

    await workflow.build_evidence(
        run=run,
        command_id="command:cmd1",
        course_id="course:abc",
        source_id="source:abc",
        role="PRIMARY",
    )

    assert run.status == "succeeded"


@pytest.mark.asyncio
async def test_api_binding_fails_if_a_different_command_claimed_the_run(
    monkeypatch,
) -> None:
    import api.course_command_service as module

    query = AsyncMock(return_value=[])
    monkeypatch.setattr(module, "repo_query", query)

    with pytest.raises(ValueError, match="claimed by another command"):
        await module.CourseCommandService._bind_command(
            "course_generation_run:one", "command:api"
        )

    assert "command = NONE OR command = $command_id" in query.await_args.args[0]


@pytest.mark.asyncio
async def test_command_load_failure_terminalizes_its_unbound_run(monkeypatch) -> None:
    import commands.course_commands as module

    workflow = SimpleNamespace(
        load_run=AsyncMock(side_effect=ValueError("claim mismatch")),
        fail_run_reference=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(module, "_workflow", workflow)
    request = module.CourseEvidenceInput.model_validate(
        {
            "run_id": "course_generation_run:one",
            "course_id": "course:abc",
            "source_id": "source:abc",
            "role": "PRIMARY",
            "execution_context": {
                "command_id": "command:cmd1",
                "execution_started_at": "2026-08-18T00:00:00Z",
                "app_name": "open_notebook",
                "command_name": "course_build_evidence",
            },
        }
    )

    with pytest.raises(ValueError, match="claim mismatch"):
        await module.course_build_evidence_command(request)

    workflow.fail_run_reference.assert_awaited_once_with(
        run_id="course_generation_run:one",
        command_id="command:cmd1",
        message="claim mismatch",
    )


@pytest.mark.asyncio
async def test_mismatched_prebound_command_cannot_terminalize_the_owner_run(
    monkeypatch,
) -> None:
    import commands.course_commands as command_module
    import open_notebook.course.workflow_service as workflow_module
    from open_notebook.course.models import CourseGenerationRun

    run = CourseGenerationRun(
        id="course_generation_run:one",
        course="course:abc",
        stage="evidence",
        adapter="open_notebook",
        model="docling",
        status="queued",
        prompt_version="evidence-v1",
        input_hash="a" * 64,
        command="command:right",
    )
    monkeypatch.setattr(
        workflow_module.CourseGenerationRun,
        "get",
        AsyncMock(return_value=run),
    )

    async def conditional_failure(statement, variables=None):
        assert "command = NONE OR command = $command_id" in statement
        if run.command in {None, str(variables["command_id"])}:
            run.status = "cancelled"
        return []

    monkeypatch.setattr(workflow_module, "repo_query", conditional_failure)
    monkeypatch.setattr(
        command_module, "_workflow", workflow_module.CourseWorkflowService()
    )
    request = command_module.CourseEvidenceInput.model_validate(
        {
            "run_id": "course_generation_run:one",
            "course_id": "course:abc",
            "source_id": "source:abc",
            "role": "PRIMARY",
            "execution_context": {
                "command_id": "command:wrong",
                "execution_started_at": "2026-08-18T00:00:00Z",
                "app_name": "open_notebook",
                "command_name": "course_build_evidence",
            },
        }
    )

    with pytest.raises(ValueError, match="binding mismatch"):
        await command_module.course_build_evidence_command(request)

    assert run.command == "command:right"
    assert run.status == "queued"


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
async def test_force_outline_runs_create_next_versions_and_replay_own_artifact(
    monkeypatch,
) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.contracts import CourseOutlineArtifact, ModelSelection
    from open_notebook.course.evidence_service import EvidenceService
    from open_notebook.course.generation_service import CourseGenerationService
    from open_notebook.course.model_adapters import FakeCourseModelAdapter
    from open_notebook.course.models import Course, CourseGenerationRun, CourseVersion

    source_hash = "a" * 64
    anchor = EvidenceService().make_anchor(
        course_id="course:force",
        source_id="source:force",
        source_sha256=source_hash,
        kind="pdf_page",
        index=1,
        block_key="one",
        quote="grounded",
        source_role="PRIMARY",
    )
    course = Course(
        id="course:force",
        title="Physics",
        notebook="notebook:force",
        status="indexing",
        source_ids=["source:force"],
        primary_source_ids=["source:force"],
    )
    artifact = CourseOutlineArtifact(
        title="Physics",
        chapters=[
            {
                "key": "one",
                    "title": "One",
                    "purpose": "Learn.",
                    "objective_keys": ["concept"],
                    "anchor_ids": [anchor.anchor_id],
            }
        ],
        concepts=[
            {"key": "concept", "label": "Concept", "anchor_ids": [anchor.anchor_id]}
        ],
    )
    adapter = FakeCourseModelAdapter(artifact)
    workflow = module.CourseWorkflowService(
        generation=CourseGenerationService(adapter=adapter)
    )
    versions: list[CourseVersion] = []

    async def save_version(self):
        if self.id is None:
            self.id = f"course_version:v{len(versions) + 1}"
        if not any(item.id == self.id for item in versions):
            versions.append(self)

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
        raise AssertionError(statement)

    async def activate(run, command_id):
        run.command = command_id
        if run.status == "queued":
            run.status = "running"
        return run

    monkeypatch.setattr(module, "repo_query", query)
    monkeypatch.setattr(module.Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(
        module.CourseVersion,
        "get",
        AsyncMock(side_effect=lambda version_id: next(v for v in versions if v.id == version_id)),
    )
    monkeypatch.setattr(module.Course, "versions", AsyncMock(return_value=versions))
    monkeypatch.setattr(module.Course, "save", AsyncMock(return_value=None))
    monkeypatch.setattr(module.CourseVersion, "save", save_version)
    monkeypatch.setattr(module.CourseGenerationRun, "save", AsyncMock(return_value=None))
    monkeypatch.setattr(workflow, "_source_hash", AsyncMock(return_value=source_hash))
    monkeypatch.setattr(workflow, "activate_run", activate)
    model = ModelSelection(
        adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
    )
    command_args = {
        "course_id": "course:force",
        "anchor_ids": [anchor.anchor_id],
        "available_lab_keys": [],
        "prompt_version": "v1",
        "model": model.model_dump(mode="json"),
    }
    logical_hash = module.generation_input_hash(
        course_id="course:force",
        stage="outline",
        command_args=command_args,
        model=model,
        prompt_version="v1",
        anchor_ids=[anchor.anchor_id],
        source_hashes={"source:force": source_hash},
    )

    def run(run_id: str) -> CourseGenerationRun:
        return CourseGenerationRun(
            id=run_id,
            course="course:force",
            stage="outline",
            adapter=model.adapter,
            model=model.model,
            reasoning_effort=model.reasoning_effort,
            status="running",
            prompt_version="v1",
            input_hash=logical_hash,
        )

    first_run = run("course_generation_run:first")
    second_run = run("course_generation_run:second")
    first = await workflow.generate_outline(
        run=first_run,
        command_id="command:first",
        course_id="course:force",
        anchor_ids=[anchor.anchor_id],
        available_lab_keys=[],
        model=model,
        prompt_version="v1",
    )
    second = await workflow.generate_outline(
        run=second_run,
        command_id="command:second",
        course_id="course:force",
        anchor_ids=[anchor.anchor_id],
        available_lab_keys=[],
        model=model,
        prompt_version="v1",
    )
    replayed_first = await workflow.generate_outline(
        run=first_run,
        command_id="command:first",
        course_id="course:force",
        anchor_ids=[anchor.anchor_id],
        available_lab_keys=[],
        model=model,
        prompt_version="v1",
    )

    assert [item.version_no for item in versions] == [1, 2]
    assert len(adapter.calls) == 2
    assert replayed_first.id == first.id
    assert first.id != second.id
    assert course.outline_version_id == second.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("persisted_status", "expected_transitions"),
    [
        ("draft", ["generating", "reviewing"]),
        ("generating", ["reviewing"]),
    ],
)
async def test_chapter_mid_save_replay_converges_to_reviewing(
    monkeypatch,
    persisted_status: str,
    expected_transitions: list[str],
) -> None:
    from open_notebook.course.models import Chapter
    from open_notebook.course.workflow_service import CourseWorkflowService

    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        title="One",
        chapter_key="one",
        status=persisted_status,
    )
    transitions: list[str] = []

    async def transition(self, target: str) -> None:
        assert self is chapter
        transitions.append(target)
        chapter.status = target

    monkeypatch.setattr(Chapter, "transition_to", transition)

    await CourseWorkflowService.advance_chapter_to_reviewing(chapter)

    assert transitions == expected_transitions
    assert chapter.status == "reviewing"


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
            command_id="command:one",
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
    link_refreshes: list[str] = []

    async def save_course(self):
        return None

    async def save_version(self):
        if self.id is None:
            self.id = f"course_version:v{len(versions) + 1}"
        if not any(item.id == self.id for item in versions):
            versions.append(self)

    async def save_chapter(self):
        if self.id is None:
            self.id = f"chapter:c{len(chapters) + 1}"
        if not any(item.id == self.id for item in chapters):
            chapters.append(self)

    async def save_lab(self):
        if self.id is None:
            self.id = f"lab:l{len(labs) + 1}"
        if not any(item.id == self.id for item in labs):
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
            return [
                item.model_dump(mode="json")
                for item in labs
                if item.chapter == str(variables["chapter"])
            ]
        if "course_validation_finding" in statement:
            return []
        if statement.lstrip().startswith("UPDATE course_note"):
            link_refreshes.append(str(variables["chapter_key"]))
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
    async def activate(run, command_id):
        run.command = command_id
        if run.status == "queued":
            run.status = "running"
        return run

    monkeypatch.setattr(workflow, "activate_run", activate)
    monkeypatch.setattr(workflow, "_source_hash", AsyncMock(return_value=source_hash))
    monkeypatch.setattr(
        workflow.evidence, "build", AsyncMock(return_value=[anchor])
    )
    selection = ModelSelection(
        adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
    )
    evidence_selection = ModelSelection(adapter="open_notebook", model="docling")
    evidence_args = {
        "course_id": "course:e2e",
        "source_id": "source:e2e",
        "role": "PRIMARY",
    }
    evidence_run = CourseGenerationRun(
        id="course_generation_run:evidence",
        course="course:e2e",
        stage="evidence",
        adapter="open_notebook",
        model="docling",
        status="running",
        prompt_version="evidence-v1",
        input_hash=module.generation_input_hash(
            course_id="course:e2e",
            stage="evidence",
            command_args=evidence_args,
            model=evidence_selection,
            prompt_version="evidence-v1",
            anchor_ids=[],
            source_hashes={"source:e2e": source_hash},
        ),
    )
    built = await workflow.build_evidence(
        run=evidence_run,
        command_id="command:evidence",
        course_id="course:e2e",
        source_id="source:e2e",
        role="PRIMARY",
    )
    replayed_evidence = await workflow.build_evidence(
        run=evidence_run,
        command_id="command:evidence",
        course_id="course:e2e",
        source_id="source:e2e",
        role="PRIMARY",
    )
    assert [item.anchor_id for item in built] == [anchor.anchor_id]
    assert [item.anchor_id for item in replayed_evidence] == [anchor.anchor_id]
    assert workflow.evidence.build.await_count == 1
    assert course.status == "indexing"
    outline_args = {
        "course_id": "course:e2e",
        "anchor_ids": [anchor.anchor_id],
        "available_lab_keys": ["limit-plot"],
        "prompt_version": "v1",
        "model": selection.model_dump(mode="json"),
    }
    outline_run = CourseGenerationRun(
        id="course_generation_run:outline",
        course="course:e2e",
        stage="outline",
        adapter=selection.adapter,
        model=selection.model,
        reasoning_effort=selection.reasoning_effort,
        status="running",
        prompt_version="v1",
        input_hash=module.generation_input_hash(
            course_id="course:e2e",
            stage="outline",
            command_args=outline_args,
            model=selection,
            prompt_version="v1",
            anchor_ids=[anchor.anchor_id],
            source_hashes={"source:e2e": source_hash},
        ),
    )
    version = await workflow.generate_outline(
        run=outline_run,
        command_id="command:outline",
        course_id="course:e2e",
        anchor_ids=[anchor.anchor_id],
        available_lab_keys=["limit-plot"],
        model=selection,
        prompt_version="v1",
    )
    await workflow.generate_outline(
        run=outline_run,
        command_id="command:outline",
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
    chapter_args = {
        "course_id": "course:e2e",
        "chapter_key": "limits",
        "anchor_ids": [anchor.anchor_id],
        "prompt_version": "v1",
        "model": selection.model_dump(mode="json"),
    }
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
        input_hash=module.generation_input_hash(
            course_id="course:e2e",
            stage="chapter_content",
            command_args=chapter_args,
            model=selection,
            prompt_version="v1",
            anchor_ids=[anchor.anchor_id],
            source_hashes={"source:e2e": source_hash},
            course_version_id=str(version.id),
            chapter_key="limits",
        ),
    )
    chapter = await workflow.generate_chapter(
        run=chapter_run,
        command_id="command:chapter",
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=selection,
        prompt_version="v1",
    )
    await workflow.generate_chapter(
        run=chapter_run,
        command_id="command:chapter",
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=selection,
        prompt_version="v1",
    )
    forced_chapter_run = chapter_run.model_copy(
        update={
            "id": "course_generation_run:chapter-force",
            "status": "running",
            "command": None,
            "output_hash": None,
        }
    )
    second_chapter = await workflow.generate_chapter(
        run=forced_chapter_run,
        command_id="command:chapter-force",
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=selection,
        prompt_version="v1",
    )
    replayed_first_chapter = await workflow.generate_chapter(
        run=chapter_run,
        command_id="command:chapter",
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=selection,
        prompt_version="v1",
    )
    assert len(adapter.calls) == 3
    assert [item.version_no for item in chapters] == [1, 2]
    assert len(labs) == 2
    assert link_refreshes == ["limits", "limits"]
    assert replayed_first_chapter.id == chapter.id
    assert second_chapter.id != chapter.id
    chapter = second_chapter

    adapter.output = ReviewArtifact(findings=[])
    review_selection = ModelSelection(
        adapter="codex_cli", model="gpt-5.6-luna", reasoning_effort="max"
    )
    review_args = {
        "course_id": "course:e2e",
        "chapter_key": "limits",
        "anchor_ids": [anchor.anchor_id],
        "prompt_version": "v1",
        "model": review_selection.model_dump(mode="json"),
    }
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
        input_hash=module.generation_input_hash(
            course_id="course:e2e",
            stage="review",
            command_args=review_args,
            model=review_selection,
            prompt_version="v1",
            anchor_ids=[anchor.anchor_id],
            source_hashes={"source:e2e": source_hash},
            course_version_id=str(version.id),
            chapter_id=str(chapter.id),
            chapter_key="limits",
        ),
    )
    reviewed, findings = await workflow.review_chapter(
        run=review_run,
        command_id="command:review",
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=review_selection,
        prompt_version="v1",
    )
    await workflow.review_chapter(
        run=review_run,
        command_id="command:review",
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=review_selection,
        prompt_version="v1",
    )
    assert findings == []
    assert len(adapter.calls) == 4
    assert reviewed.status == "ready"

    published = await CourseService.publish_chapter(
        "course:e2e", str(version.id), str(chapter.id)
    )
    assert published.status == "published"
    side_effect_counts = (len(adapter.calls), len(labs), len(link_refreshes))
    replayed_published = await workflow.generate_chapter(
        run=forced_chapter_run,
        command_id="command:chapter-force",
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=selection,
        prompt_version="v1",
    )
    replayed_review, replayed_findings = await workflow.review_chapter(
        run=review_run,
        command_id="command:review",
        course_id="course:e2e",
        chapter_key="limits",
        anchor_ids=[anchor.anchor_id],
        model=review_selection,
        prompt_version="v1",
    )
    assert replayed_published.id == published.id
    assert replayed_review.id == published.id
    assert replayed_findings == []
    assert (len(adapter.calls), len(labs), len(link_refreshes)) == side_effect_counts
