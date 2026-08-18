"""Course worker orchestration contracts and registration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
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
            payload = cast(dict[str, object], variables["payload"])
            row = {"id": run_id, **payload}
            self.runs[run_id] = row
            return [dict(row)]
        if "FROM command WHERE" in statement and "args.run_id" in statement:
            rows = [
                dict(row)
                for row in self.commands.values()
                if cast(dict[str, object], row["args"])["run_id"]
                == variables["run_id"]
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
    "cause",
    [
        TimeoutError("late"),
        ConnectionError("offline"),
        httpx.ReadTimeout("provider timed out"),
        httpx.ConnectError("provider offline"),
    ],
)
def test_adapter_failure_retry_uses_typed_transient_cause(cause: Exception) -> None:
    from commands.course_commands import (
        AdapterFailureDisposition,
        _adapter_failure_disposition,
    )
    from open_notebook.course.model_adapters import AdapterError

    failure = AdapterError("sanitized provider failure")
    failure.__cause__ = cause

    assert (
        _adapter_failure_disposition(failure)
        is AdapterFailureDisposition.TRANSIENT
    )


@pytest.mark.parametrize(
    "cause",
    [None, FileNotFoundError("missing CLI"), ValueError("invalid output")],
)
def test_adapter_failure_defaults_to_permanent_without_typed_network_cause(
    cause: Exception | None,
) -> None:
    from commands.course_commands import (
        AdapterFailureDisposition,
        _adapter_failure_disposition,
    )
    from open_notebook.course.model_adapters import AdapterError

    # The text deliberately says "timed out": retryability must not depend on it.
    failure = AdapterError("Codex CLI timed out after 1800 seconds.")
    failure.__cause__ = cause

    assert (
        _adapter_failure_disposition(failure)
        is AdapterFailureDisposition.PERMANENT
    )


@pytest.mark.asyncio
async def test_adapter_failure_handler_terminalizes_only_permanent_failures(
    monkeypatch,
) -> None:
    import commands.course_commands as module
    from open_notebook.course.model_adapters import AdapterError

    input_data = module.CourseEvidenceInput.model_validate(
        {
            "run_id": "course_generation_run:one",
            "course_id": "course:one",
            "source_id": "source:one",
            "role": "PRIMARY",
        }
    )
    permanent = AsyncMock()
    monkeypatch.setattr(module, "_permanent_failure", permanent)

    permanent_failure = AdapterError("authentication failed")
    with pytest.raises(ValueError, match="authentication failed"):
        await module._handle_adapter_failure(input_data, permanent_failure)
    permanent.assert_awaited_once_with(input_data, permanent_failure)

    permanent.reset_mock()
    transient_failure = AdapterError("provider unavailable")
    transient_failure.__cause__ = TimeoutError("late")
    with pytest.raises(AdapterError) as caught:
        await module._handle_adapter_failure(input_data, transient_failure)
    assert caught.value is transient_failure
    permanent.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_adapter_failure_retries_then_synchronizes_final_failure(
    monkeypatch,
) -> None:
    import commands.course_commands as module
    from open_notebook.course.model_adapters import AdapterError

    input_data = module.CourseEvidenceInput.model_validate(
        {
            "run_id": "course_generation_run:one",
            "course_id": "course:one",
            "source_id": "source:one",
            "role": "PRIMARY",
        }
    )

    def transient_failure() -> AdapterError:
        failure = AdapterError("provider unavailable")
        failure.__cause__ = TimeoutError("late")
        return failure

    operation = AsyncMock(
        side_effect=[transient_failure(), transient_failure(), transient_failure()]
    )
    permanent = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr(module, "_permanent_failure", permanent)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)

    with pytest.raises(ValueError, match="provider unavailable"):
        await module._execute_course_operation(input_data, operation)

    assert operation.await_count == 3
    assert sleep.await_count == 2
    permanent.assert_awaited_once()


@pytest.mark.asyncio
async def test_transient_adapter_retry_reuses_selection_and_can_recover(
    monkeypatch,
) -> None:
    import commands.course_commands as module
    from open_notebook.course.model_adapters import AdapterError

    input_data = module.CourseEvidenceInput.model_validate(
        {
            "run_id": "course_generation_run:one",
            "course_id": "course:one",
            "source_id": "source:one",
            "role": "PRIMARY",
        }
    )
    failure = AdapterError("provider unavailable")
    failure.__cause__ = ConnectionError("offline")
    expected = object()
    operation = AsyncMock(side_effect=[failure, expected])
    permanent = AsyncMock()
    monkeypatch.setattr(module, "_permanent_failure", permanent)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())

    assert await module._execute_course_operation(input_data, operation) is expected
    assert operation.await_count == 2
    permanent.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_failure_after_activation_terminalizes_run_without_retry(
    monkeypatch,
) -> None:
    import commands.course_commands as module
    from open_notebook.course.models import CourseGenerationRun

    run = CourseGenerationRun(
        id="course_generation_run:runtime-failure",
        course="course:one",
        course_version="course_version:one",
        chapter_key="motion",
        stage="chapter_content",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        reasoning_effort="max",
        status="running",
        prompt_version="v1",
        input_hash="a" * 64,
        command="command:one",
    )
    request = module.CourseChapterInput.model_validate(
        {
            "run_id": str(run.id),
            "course_id": str(run.course),
            "chapter_key": "motion",
            "anchor_ids": ["anchor:one"],
            "prompt_version": "v1",
            "model": {
                "adapter": "codex_cli",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "max",
            },
            "execution_context": {
                "command_id": "command:one",
                "execution_started_at": "2026-08-18T00:00:00Z",
                "app_name": "open_notebook",
                "command_name": "course_generate_chapter",
            },
        }
    )
    generate = AsyncMock(side_effect=RuntimeError("Lab.save failed"))
    monkeypatch.setattr(module._workflow, "load_run", AsyncMock(return_value=run))
    monkeypatch.setattr(module._workflow, "generate_chapter", generate)

    async def terminalize(**kwargs):
        run.status = "failed"
        run.error_message = str(kwargs["message"])

    monkeypatch.setattr(module._workflow, "fail_run_reference", terminalize)

    with pytest.raises(ValueError, match="Lab.save failed"):
        await module.course_generate_chapter_command(request)

    assert run.status == "failed"
    assert run.error_message == "Lab.save failed"
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_lab_save_failure_keeps_last_successful_chapter_current(
    monkeypatch,
) -> None:
    import api.course_service as service_module
    import commands.course_commands as command_module
    import open_notebook.course.workflow_service as workflow_module
    from api.course_command_service import CourseCommandService
    from open_notebook.course.contracts import (
        ChapterArtifact,
        ChapterSection,
        CourseOutlineArtifact,
        ExerciseArtifact,
        FormulaArtifact,
        FunctionPlotLabSpec,
        ModelSelection,
        WorkedExampleArtifact,
    )
    from open_notebook.course.generation_service import CourseGenerationService
    from open_notebook.course.model_adapters import FakeCourseModelAdapter
    from open_notebook.course.models import (
        Chapter,
        Course,
        CourseGenerationRun,
        CourseVersion,
        Lab,
    )

    outline = CourseOutlineArtifact(
        title="Calculus",
        chapters=[
            {
                "key": "limits",
                "title": "Limits",
                "purpose": "Learn limits.",
                "objective_keys": ["limit"],
                "anchor_ids": ["anchor:one"],
                "lab_keys": ["limit-plot"],
            }
        ],
        concepts=[
            {
                "key": "limit",
                "label": "Limit",
                "anchor_ids": ["anchor:one"],
            }
        ],
    )
    artifact = ChapterArtifact(
        chapter_key="limits",
        purpose="Learn limits.",
        prerequisites=["algebra"],
        objectives=["Evaluate limits"],
        sections=[
            ChapterSection(
                key="intro",
                title="Introduction",
                markdown="Grounded.",
                anchor_ids=["anchor:one"],
            )
        ],
        definitions=["Limit"],
        formulas=[
            FormulaArtifact(
                key="identity",
                latex="x",
                meaning="Identity",
                anchor_ids=["anchor:one"],
                oracle_expression="x",
            )
        ],
        worked_examples=[
            WorkedExampleArtifact(
                key="example",
                prompt="Compute 2 + 2.",
                steps=["Add."],
                answer="4",
                anchor_ids=["anchor:one"],
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
                anchor_ids=["anchor:one"],
            )
        ],
        quick_reference=["lim"],
        citations=["anchor:one"],
    )
    outline_payload = outline.model_dump(mode="json")
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="generating",
        outline_version_id="course_version:one",
    )
    version = CourseVersion(
        id="course_version:one",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=outline_payload,
        outline_hash=hashlib.sha256(
            json.dumps(
                outline_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    previous = Chapter(
        id="chapter:published",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        version_no=1,
        title="Limits",
        status="published",
        artifact=artifact.model_dump(mode="json"),
    )
    selection = ModelSelection(
        adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
    )
    run = CourseGenerationRun(
        id="course_generation_run:failed-lab",
        course="course:one",
        course_version="course_version:one",
        chapter_key="limits",
        stage="chapter_content",
        adapter=selection.adapter,
        model=selection.model,
        reasoning_effort=selection.reasoning_effort,
        status="running",
        prompt_version="v1",
        input_hash="a" * 64,
        command="command:one",
    )
    generated: list[Chapter] = []
    workflow = workflow_module.CourseWorkflowService(
        generation=CourseGenerationService(adapter=FakeCourseModelAdapter(artifact))
    )

    async def save_chapter(self):
        if self.id is None:
            self.id = "chapter:partial"
            generated.append(self)

    async def query(statement: str, variables=None):
        del variables
        if "FROM chapter WHERE course_version" in statement:
            return [
                item.model_dump(mode="json")
                for item in generated
                if item.input_hash == workflow_module.artifact_replay_hash(run)
            ]
        if statement.lstrip().startswith("UPDATE $run_id SET chapter"):
            run.chapter = "chapter:partial"
            return [run.model_dump(mode="json")]
        if "SELECT * FROM lab WHERE chapter" in statement:
            return []
        if statement.lstrip().startswith("SELECT * FROM course_generation_run"):
            return [run.model_dump(mode="json")]
        raise AssertionError(statement)

    async def fail_run_reference(**kwargs):
        run.status = "failed"
        run.error_message = str(kwargs["message"])

    monkeypatch.setattr(workflow_module, "repo_query", query)
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(
        CourseVersion,
        "chapters",
        AsyncMock(side_effect=lambda _version_id: [previous, *generated]),
    )
    monkeypatch.setattr(Chapter, "save", save_chapter)
    monkeypatch.setattr(Lab, "save", AsyncMock(side_effect=RuntimeError("Lab.save failed")))
    monkeypatch.setattr(
        workflow,
        "grounded_inputs",
        AsyncMock(return_value=([], {"source:one": "b" * 64}, [])),
    )
    monkeypatch.setattr(workflow, "validate_run_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "validate_run_claim", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "activate_run", AsyncMock(return_value=run))
    monkeypatch.setattr(workflow, "load_run", AsyncMock(return_value=run))
    monkeypatch.setattr(workflow, "fail_run_reference", fail_run_reference)
    monkeypatch.setattr(command_module, "_workflow", workflow)
    request = command_module.CourseChapterInput.model_validate(
        {
            "run_id": str(run.id),
            "course_id": "course:one",
            "chapter_key": "limits",
            "anchor_ids": ["anchor:one"],
            "prompt_version": "v1",
            "model": selection.model_dump(mode="json"),
            "execution_context": {
                "command_id": "command:one",
                "execution_started_at": "2026-08-18T00:00:00Z",
                "app_name": "open_notebook",
                "command_name": "course_generate_chapter",
            },
        }
    )

    with pytest.raises(ValueError, match="Lab.save failed"):
        await command_module.course_generate_chapter_command(request)

    assert len(generated) == 1
    assert run.status == "failed"
    assert run.chapter == "chapter:partial"
    monkeypatch.setattr(
        service_module,
        "repo_query",
        AsyncMock(return_value=[run.model_dump(mode="json")]),
    )

    current = await CourseCommandService.current_chapter("course:one", "limits")

    assert current.id == "chapter:published"


def test_course_command_registry_import_failure_is_fatal() -> None:
    from api.course_command_registry import ensure_course_commands_registered

    def fail_import(_module_name: str) -> None:
        raise ImportError("commands unavailable")

    with pytest.raises(RuntimeError, match="import Course commands"):
        ensure_course_commands_registered(importer=fail_import)


def test_course_command_registry_missing_required_command_is_fatal() -> None:
    from api.course_command_registry import ensure_course_commands_registered

    registered = [
        SimpleNamespace(app_id="open_notebook", name="course_build_evidence"),
        SimpleNamespace(app_id="open_notebook", name="course_generate_outline"),
        SimpleNamespace(app_id="open_notebook", name="course_generate_chapter"),
    ]

    with pytest.raises(RuntimeError, match="course_review_chapter"):
        ensure_course_commands_registered(
            importer=lambda _module_name: None,
            registered_commands=lambda: registered,
        )


def test_api_startup_fails_when_course_registry_is_incomplete() -> None:
    script = """
from surreal_commands import registry
registry.get_all_commands = lambda: []
try:
    import api.main
except RuntimeError as exc:
    print(str(exc))
else:
    raise SystemExit('api.main continued with an incomplete Course registry')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Missing required Course commands" in completed.stdout


@pytest.mark.parametrize(
    ("current", "framework", "expected"),
    [
        ("queued", "new", None),
        ("running", "new", None),
        ("succeeded", "running", None),
        ("failed", "completed", None),
        ("cancelled", "running", None),
        ("queued", "running", "running"),
        ("queued", "failed", "failed"),
        ("running", "completed", "succeeded"),
    ],
)
def test_course_run_status_mapping_is_monotonic(
    current: str,
    framework: str,
    expected: str | None,
) -> None:
    from api.course_command_service import next_course_run_status

    assert next_course_run_status(current, framework) == expected


@pytest.mark.asyncio
async def test_active_run_sync_ignores_stale_new_and_terminal_polling(
    monkeypatch,
) -> None:
    import api.course_command_service as module

    service = module.CourseCommandService()
    set_status = AsyncMock()
    monkeypatch.setattr(service, "_set_run_status", set_status)

    monkeypatch.setattr(
        service, "_framework_status", AsyncMock(return_value=("new", None))
    )
    running = {"id": "course_generation_run:one", "command": "command:one", "status": "running"}
    assert await service._sync_active_row(running) == running
    set_status.assert_not_awaited()

    monkeypatch.setattr(
        service, "_framework_status", AsyncMock(return_value=("running", None))
    )
    succeeded = {**running, "status": "succeeded"}
    assert await service._sync_active_row(succeeded) == succeeded
    set_status.assert_not_awaited()


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
async def test_complete_run_cas_miss_preserves_failed_terminal(monkeypatch) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.models import CourseGenerationRun

    stale = CourseGenerationRun(
        id="course_generation_run:stale-complete",
        course="course:one",
        stage="outline",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        reasoning_effort="max",
        status="running",
        prompt_version="v1",
        input_hash="a" * 64,
        command="command:one",
    )
    terminal = stale.model_copy(update={"status": "failed", "error_message": "boom"})

    async def cas(statement: str, variables=None):
        assert "WHERE status = 'running'" in statement
        assert variables is not None
        return []

    save = AsyncMock()
    monkeypatch.setattr(module, "repo_query", cas)
    monkeypatch.setattr(
        module.CourseGenerationRun, "get", AsyncMock(return_value=terminal)
    )
    monkeypatch.setattr(module.CourseGenerationRun, "save", save)

    with pytest.raises(ValueError, match="no longer active"):
        await module.CourseWorkflowService.complete_run(stale, {"title": "result"})

    assert stale.status == "failed"
    assert stale.output_hash is None
    assert stale.error_message == "boom"
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_run_cas_miss_preserves_succeeded_terminal(monkeypatch) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.models import CourseGenerationRun

    stale = CourseGenerationRun(
        id="course_generation_run:stale-failure",
        course="course:one",
        stage="outline",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        reasoning_effort="max",
        status="running",
        prompt_version="v1",
        input_hash="a" * 64,
        command="command:one",
    )
    terminal = stale.model_copy(
        update={"status": "succeeded", "output_hash": "b" * 64}
    )

    async def cas(statement: str, variables=None):
        assert "WHERE status = 'running'" in statement
        assert variables is not None
        return []

    save = AsyncMock()
    monkeypatch.setattr(module, "repo_query", cas)
    monkeypatch.setattr(
        module.CourseGenerationRun, "get", AsyncMock(return_value=terminal)
    )
    monkeypatch.setattr(module.CourseGenerationRun, "save", save)

    await module.CourseWorkflowService.fail_run(stale, "late failure")

    assert stale.status == "succeeded"
    assert stale.output_hash == "b" * 64
    assert stale.error_message is None
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_chapter_completion_and_stable_links_are_one_atomic_promotion(
    monkeypatch,
) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.contracts import ChapterArtifact, ChapterSection
    from open_notebook.course.models import Chapter, CourseGenerationRun, CourseVersion

    artifact = ChapterArtifact(
        chapter_key="limits",
        purpose="Learn limits.",
        objectives=["Understand limits"],
        sections=[
            ChapterSection(
                key="intro",
                title="Introduction",
                markdown="Grounded.",
                anchor_ids=["anchor:one"],
            )
        ],
    )
    chapter = Chapter(
        id="chapter:partial",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        version_no=2,
        title="Limits",
        status="reviewing",
        artifact=artifact.model_dump(mode="json"),
    )
    stale = CourseGenerationRun(
        id="course_generation_run:partial",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:partial",
        chapter_key="limits",
        stage="chapter_content",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        reasoning_effort="max",
        status="running",
        prompt_version="v1",
        input_hash="a" * 64,
        command="command:one",
    )
    terminal = stale.model_copy(update={"status": "failed", "error_message": "race"})
    statements: list[str] = []

    async def conflict(statement: str, variables=None):
        assert variables is not None
        statements.append(" ".join(statement.split()))
        raise RuntimeError("Course generation run completion conflict")

    monkeypatch.setattr(module, "repo_query", conflict)
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[chapter])
    )
    monkeypatch.setattr(
        module.CourseGenerationRun, "get", AsyncMock(return_value=terminal)
    )

    with pytest.raises(ValueError, match="no longer active"):
        await module.CourseWorkflowService.complete_chapter_run(
            run=stale,
            chapter=chapter,
            artifact=artifact,
        )

    assert len(statements) == 1
    assert "BEGIN TRANSACTION" in statements[0]
    assert statements[0].index("UPDATE course_version") < statements[0].index(
        "UPDATE course_generation_run"
    )
    assert "status = 'generating'" in statements[0]
    assert statements[0].index("WHERE id = $run_id AND status = 'running'") < statements[
        0
    ].index("UPDATE course_note")
    assert "THROW 'Course generation run completion conflict'" in statements[0]
    assert "COMMIT TRANSACTION" in statements[0]
    assert stale.status == "failed"


@pytest.mark.asyncio
async def test_stable_link_promotion_uses_exact_legacy_current_snapshot(
    monkeypatch,
) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.contracts import ChapterArtifact, ChapterSection
    from open_notebook.course.models import Chapter, CourseGenerationRun, CourseVersion

    old_artifact = ChapterArtifact(
        chapter_key="limits",
        purpose="Old limits.",
        objectives=["Understand old limits"],
        sections=[
            ChapterSection(
                key="old",
                title="Old",
                markdown="Old grounded content.",
                anchor_ids=["anchor:one"],
            )
        ],
    )
    new_artifact = ChapterArtifact(
        chapter_key="limits",
        purpose="New limits.",
        objectives=["Understand new limits"],
        sections=[
            ChapterSection(
                key="new",
                title="New",
                markdown="New grounded content.",
                anchor_ids=["anchor:one"],
            )
        ],
    )
    completing_run = CourseGenerationRun(
        id="course_generation_run:old",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:old",
        chapter_key="limits",
        stage="chapter_content",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        status="running",
        prompt_version="v1",
        input_hash="old-claim",
        command="command:old",
    )
    old_chapter = Chapter(
        id="chapter:old",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        version_no=1,
        title="Old limits",
        status="reviewing",
        input_hash=module.artifact_replay_hash(completing_run),
        artifact=old_artifact.model_dump(mode="json"),
    )
    legacy_run = CourseGenerationRun(
        id="course_generation_run:legacy",
        course="course:one",
        course_version="course_version:one",
        chapter=None,
        chapter_key="limits",
        stage="chapter_content",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        status="succeeded",
        prompt_version="v1",
        input_hash="legacy-claim",
    )
    new_chapter = Chapter(
        id="chapter:new",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        version_no=2,
        title="New limits",
        status="published",
        input_hash=module.artifact_replay_hash(legacy_run),
        artifact=new_artifact.model_dump(mode="json"),
    )
    legacy_run.output_hash = module._artifact_hash(
        {"output": new_chapter.artifact or {}}
    )
    transaction_variables: dict[str, object] = {}

    async def query(statement: str, variables=None):
        if statement.lstrip().startswith("SELECT * FROM course_generation_run"):
            return [legacy_run.model_dump(mode="json")]
        assert statement.lstrip().startswith("BEGIN TRANSACTION")
        transaction_variables.update(variables or {})
        return []

    monkeypatch.setattr(module, "repo_query", query)
    monkeypatch.setattr(
        CourseVersion,
        "chapters",
        AsyncMock(return_value=[old_chapter, new_chapter]),
    )

    await module.CourseWorkflowService.complete_chapter_run(
        run=completing_run,
        chapter=old_chapter,
        artifact=old_artifact,
    )

    assert transaction_variables["refresh_stable_links"] is False
    known_succeeded_run_ids = transaction_variables["known_succeeded_run_ids"]
    assert isinstance(known_succeeded_run_ids, list)
    assert [str(item) for item in known_succeeded_run_ids] == [
        "course_generation_run:legacy"
    ]


@pytest.mark.asyncio
async def test_evidence_replay_hash_uses_immutable_canonical_anchor_output(
    monkeypatch,
) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.contracts import ModelSelection
    from open_notebook.course.evidence_service import EvidenceService
    from open_notebook.course.models import Course, CourseGenerationRun

    source_hash = "a" * 64
    course = Course(
        id="course:canonical-evidence",
        title="Physics",
        notebook="notebook:one",
        status="indexing",
        source_ids=["source:one"],
        primary_source_ids=["source:one"],
    )
    evidence = EvidenceService()
    anchors = [
        evidence.make_anchor(
            course_id=str(course.id),
            source_id="source:one",
            source_sha256=source_hash,
            kind="pdf_page",
            index=index,
            block_key=key,
            quote=quote,
            source_role="PRIMARY",
        )
        for index, key, quote in ((1, "a", "first"), (2, "b", "second"))
    ]
    selection = ModelSelection(adapter="open_notebook", model="docling")
    command_args = {
        "course_id": str(course.id),
        "source_id": "source:one",
        "role": "PRIMARY",
    }
    run = CourseGenerationRun(
        id="course_generation_run:canonical-evidence",
        course=str(course.id),
        stage="evidence",
        adapter=selection.adapter,
        model=selection.model,
        status="running",
        prompt_version="evidence-v1",
        input_hash=module.generation_input_hash(
            course_id=str(course.id),
            stage="evidence",
            command_args=command_args,
            model=selection,
            prompt_version="evidence-v1",
            anchor_ids=[],
            source_hashes={"source:one": source_hash},
        ),
    )
    persisted_rows = []
    for number, anchor in enumerate(anchors, start=1):
        row = anchor.model_dump(mode="json")
        row.update(
            {
                "id": f"course_evidence_anchor:{number}",
                "evidence": "evidence:one",
                "created": "2026-08-18T00:00:00Z",
                "updated": "2026-08-18T00:01:00Z",
            }
        )
        persisted_rows.append(row)

    async def query(statement, variables=None):
        variables = variables or {}
        if "FROM course_evidence_anchor" in statement:
            # Deliberately differs from the build order and includes DB envelope data.
            return list(reversed(persisted_rows))
        if statement.lstrip().startswith("UPDATE $run_id"):
            run.status = "succeeded"
            run.output_hash = str(variables["output_hash"])
            return [run.model_dump(mode="json")]
        raise AssertionError(statement)

    async def activate(active_run, _command_id):
        return active_run

    workflow = module.CourseWorkflowService()
    monkeypatch.setattr(module.Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(module, "repo_query", query)
    monkeypatch.setattr(workflow, "activate_run", activate)
    monkeypatch.setattr(workflow, "_source_hash", AsyncMock(return_value=source_hash))
    evidence_build = AsyncMock(return_value=anchors)
    monkeypatch.setattr(workflow.evidence, "build", evidence_build)
    monkeypatch.setattr(module.CourseGenerationRun, "save", AsyncMock(return_value=None))

    built = await workflow.build_evidence(
        run=run,
        command_id="command:evidence",
        course_id=str(course.id),
        source_id="source:one",
        role="PRIMARY",
    )
    replayed = await workflow.build_evidence(
        run=run,
        command_id="command:evidence",
        course_id=str(course.id),
        source_id="source:one",
        role="PRIMARY",
    )

    assert {item.anchor_id for item in built} == {item.anchor_id for item in replayed}
    assert evidence_build.await_count == 1


@pytest.mark.asyncio
async def test_review_replay_hash_ignores_row_order_metadata_and_human_resolution(
    monkeypatch,
) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.contracts import (
        CourseOutlineArtifact,
        ModelSelection,
        ReviewArtifact,
        ValidationFinding,
    )
    from open_notebook.course.models import (
        Chapter,
        Course,
        CourseGenerationRun,
        CourseVersion,
    )

    source_hash = "b" * 64
    anchor_ids = ["anchor:one"]
    course = Course(
        id="course:canonical-review",
        title="Physics",
        notebook="notebook:one",
        status="outline_approved",
        outline_version_id="course_version:one",
    )
    outline = CourseOutlineArtifact(
        title="Physics",
        chapters=[
            {
                "key": "motion",
                "title": "Motion",
                "purpose": "Learn motion.",
                "objective_keys": ["motion"],
                "anchor_ids": anchor_ids,
            }
        ],
        concepts=[
            {"key": "motion", "label": "Motion", "anchor_ids": anchor_ids}
        ],
    )
    version = CourseVersion(
        id="course_version:one",
        course=str(course.id),
        version_no=1,
        status="generating",
        outline_artifact=outline.model_dump(mode="json"),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    chapter = Chapter(
        id="chapter:one",
        course_version=str(version.id),
        chapter_no=1,
        chapter_key="motion",
        title="Motion",
        status="reviewing",
        artifact={
            "chapter_key": "motion",
            "purpose": "Learn motion.",
            "objectives": ["Understand motion"],
            "sections": [
                {
                    "key": "core",
                    "title": "Core",
                    "markdown": "Grounded.",
                    "anchor_ids": anchor_ids,
                    "provenance": "derived",
                }
            ],
        },
    )
    original_findings = [
        ValidationFinding(
            kind="review",
            severity="info",
            item_key=item_key,
            anchor_ids=anchor_ids,
            message=message,
        )
        for item_key, message in (("a", "First"), ("b", "Second"))
    ]
    review = AsyncMock(return_value=ReviewArtifact(findings=original_findings))
    generation = SimpleNamespace(
        review=review,
        validate_chapter=lambda _artifact, _anchors: [],
        assert_publishable=lambda _findings: None,
    )
    workflow = module.CourseWorkflowService(
        generation=cast(module.CourseGenerationService, generation)
    )
    selection = ModelSelection(
        adapter="codex_cli", model="gpt-5.6-luna", reasoning_effort="max"
    )
    command_args = {
        "course_id": str(course.id),
        "chapter_key": "motion",
        "anchor_ids": anchor_ids,
        "prompt_version": "v1",
        "model": selection.model_dump(mode="json"),
    }
    run = CourseGenerationRun(
        id="course_generation_run:canonical-review",
        course=str(course.id),
        course_version=str(version.id),
        chapter=str(chapter.id),
        chapter_key="motion",
        stage="review",
        adapter=selection.adapter,
        model=selection.model,
        reasoning_effort=selection.reasoning_effort,
        status="running",
        prompt_version="v1",
        input_hash=module.generation_input_hash(
            course_id=str(course.id),
            stage="review",
            command_args=command_args,
            model=selection,
            prompt_version="v1",
            anchor_ids=anchor_ids,
            source_hashes={"source:one": source_hash},
            course_version_id=str(version.id),
            chapter_id=str(chapter.id),
            chapter_key="motion",
        ),
    )
    persisted_rows: list[dict[str, object]] = []

    async def query(statement, variables=None):
        variables = variables or {}
        if statement.lstrip().startswith("DELETE course_validation_finding"):
            persisted_rows.clear()
            return []
        if "UPSERT $finding_id" in statement:
            persisted_rows.append(
                {
                    "id": str(variables["finding_id"]),
                    "generation_run": str(run.id),
                    "finding": dict(variables["finding"]),
                    "created": "2026-08-18T00:00:00Z",
                    "updated": "2026-08-18T00:01:00Z",
                }
            )
            return []
        if "FROM course_validation_finding" in statement:
            return list(reversed(persisted_rows))
        if statement.lstrip().startswith("UPDATE $run_id"):
            run.status = "succeeded"
            run.output_hash = str(variables["output_hash"])
            return [run.model_dump(mode="json")]
        raise AssertionError(statement)

    async def activate(active_run, _command_id):
        return active_run

    monkeypatch.setattr(workflow, "approved_version", AsyncMock(return_value=(version, outline)))
    monkeypatch.setattr(
        workflow,
        "grounded_inputs",
        AsyncMock(return_value=([], {"source:one": source_hash}, [])),
    )
    monkeypatch.setattr(workflow, "activate_run", activate)
    monkeypatch.setattr(module, "repo_query", query)
    monkeypatch.setattr(module.Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(module.CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(module.Chapter, "get", AsyncMock(return_value=chapter))
    monkeypatch.setattr(module.Chapter, "save", AsyncMock(return_value=None))
    monkeypatch.setattr(module.CourseGenerationRun, "save", AsyncMock(return_value=None))

    _, initial = await workflow.review_chapter(
        run=run,
        command_id="command:review",
        course_id=str(course.id),
        chapter_key="motion",
        anchor_ids=anchor_ids,
        model=selection,
        prompt_version="v1",
    )
    for index, row in enumerate(persisted_rows):
        finding = dict(cast(dict[str, object], row["finding"]))
        finding["status"] = "resolved"
        finding["resolution_reason"] = f"Human resolution {index}"
        row["finding"] = finding

    _, replayed = await workflow.review_chapter(
        run=run,
        command_id="command:review",
        course_id=str(course.id),
        chapter_key="motion",
        anchor_ids=anchor_ids,
        model=selection,
        prompt_version="v1",
    )

    assert [finding.item_key for finding in initial] == ["a", "b"]
    assert {finding.status for finding in replayed} == {"resolved"}
    assert review.await_count == 1


@pytest.mark.asyncio
async def test_persistent_active_run_dedupe_and_ordered_key(monkeypatch) -> None:
    import api.course_command_service as module

    store = _FakeQueueStore()
    monkeypatch.setattr(module, "repo_query", store.query)
    monkeypatch.setattr(module, "submit_command", store.submit)
    service = module.CourseCommandService()
    common: dict[str, Any] = {
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
    kwargs: dict[str, Any] = {
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
    kwargs: dict[str, Any] = {
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
    kwargs: dict[str, Any] = {
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
    tampered_args = dict(
        cast(dict[str, object], store.commands["command:cmd1"]["args"])
    )
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
        variables = variables or {}
        if "SET command = $command_id" in statement:
            assert run.command is None
            run.command = str(variables["command_id"])
            run.status = "running"
            return [run.model_dump(mode="json")]
        if statement.lstrip().startswith("UPDATE $run_id"):
            run.status = "succeeded"
            run.output_hash = str(variables["output_hash"])
            return [run.model_dump(mode="json")]
        raise AssertionError(statement)

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

    assert (
        "command = NONE OR command = $command_id"
        in query.await_args_list[0].args[0]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
async def test_api_binding_cannot_attach_command_to_terminalized_unbound_run(
    monkeypatch,
    terminal_status: str,
) -> None:
    import api.course_command_service as module

    row = {
        "id": "course_generation_run:one",
        "status": terminal_status,
        "command": None,
    }

    async def query(statement, variables=None):
        del variables
        if statement.lstrip().startswith("UPDATE $run_id SET command"):
            assert "status IN ['queued', 'running']" in statement
            return []
        if statement.lstrip().startswith("SELECT * FROM $run_id"):
            return [row]
        raise AssertionError(statement)

    monkeypatch.setattr(module, "repo_query", query)

    with pytest.raises(ValueError, match=terminal_status):
        await module.CourseCommandService._bind_command(
            "course_generation_run:one", "command:api"
        )

    assert row["command"] is None


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
    import open_notebook.domain.base as base_module
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

    async def create_record(table, data):
        assert table == "course_version"
        row = {
            **data,
            "id": f"course_version:v{len(versions) + 1}",
        }
        versions.append(CourseVersion(**row))
        return row

    async def update_record(_table, record_id, _data):
        # This mirrors SurrealDB UPDATE semantics: updating a deterministic ID
        # that has not been created returns no row.
        assert not any(str(item.id) == str(record_id) for item in versions)
        return []

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
        if statement.lstrip().startswith("UPDATE $run_id"):
            target = next(
                item
                for item in (first_run, second_run)
                if str(module.ensure_record_id(str(item.id)))
                == str(variables["run_id"])
            )
            target.status = "succeeded"
            target.output_hash = str(variables["output_hash"])
            return [target.model_dump(mode="json")]
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
    monkeypatch.setattr(module.CourseGenerationRun, "save", AsyncMock(return_value=None))
    monkeypatch.setattr(base_module, "repo_create", create_record)
    monkeypatch.setattr(base_module, "repo_update", update_record)
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
    query_call = query.await_args
    assert query_call is not None
    assert "status = $run_status" in query_call.args[0]
    assert "status = 'queued'" in query_call.args[0]
    assert "status = 'running'" in query_call.args[0]
    assert query_call.args[1]["run_status"] == "failed"


@pytest.mark.asyncio
async def test_generic_command_stale_new_status_never_regresses_running_run(
    monkeypatch,
) -> None:
    import api.command_service as module

    monkeypatch.setattr(
        module,
        "get_command_status",
        AsyncMock(
            return_value=SimpleNamespace(
                status="new",
                result=None,
                error_message=None,
                created=None,
                updated=None,
                progress=None,
            )
        ),
    )
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(module, "repo_query", query, raising=False)

    result = await module.CommandService.get_command_status("command:abc")

    assert result["status"] == "new"
    query.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("version_status", ["generating", "published"])
async def test_active_outline_replay_of_approved_artifact_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
    version_status: str,
) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.contracts import CourseOutlineArtifact, ModelSelection
    from open_notebook.course.models import Course, CourseGenerationRun, CourseVersion

    outline = CourseOutlineArtifact(
        title="Calculus",
        chapters=[
            {
                "key": "limits",
                "title": "Limits",
                "purpose": "Learn limits.",
                "objective_keys": ["limit"],
                "anchor_ids": ["anchor:one"],
            }
        ],
        concepts=[
            {
                "key": "limit",
                "label": "Limit",
                "anchor_ids": ["anchor:one"],
            }
        ],
    )
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="ready",
        outline_version_id="course_version:approved",
    )
    selection = ModelSelection(
        adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
    )
    run = CourseGenerationRun(
        id="course_generation_run:outline-replay",
        course="course:one",
        stage="outline",
        adapter=selection.adapter,
        model=selection.model,
        reasoning_effort=selection.reasoning_effort,
        status="running",
        prompt_version="v1",
        input_hash="logical-hash",
    )
    version = CourseVersion(
        id="course_version:approved",
        course="course:one",
        version_no=1,
        status=version_status,
        outline_artifact=outline.model_dump(mode="json"),
        input_hash=module.artifact_replay_hash(run),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    generate = AsyncMock(side_effect=AssertionError("model must not run"))
    generation = SimpleNamespace(generate_outline=generate)
    workflow = module.CourseWorkflowService(
        generation=cast(module.CourseGenerationService, generation)
    )
    monkeypatch.setattr(module.Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(
        module.CourseVersion, "get", AsyncMock(return_value=version)
    )
    async def replay_query(statement: str, variables=None):
        variables = variables or {}
        if "FROM course_version WHERE" in statement:
            return [version.model_dump(mode="json")]
        if statement.lstrip().startswith("UPDATE $run_id"):
            run.status = "succeeded"
            run.output_hash = str(variables["output_hash"])
            return [run.model_dump(mode="json")]
        raise AssertionError(statement)

    monkeypatch.setattr(module, "repo_query", replay_query)
    monkeypatch.setattr(
        workflow,
        "grounded_inputs",
        AsyncMock(return_value=([], {"source:one": "a" * 64}, [])),
    )
    monkeypatch.setattr(workflow, "validate_run_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "validate_run_claim", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "activate_run", AsyncMock(return_value=run))
    course_save = AsyncMock()
    monkeypatch.setattr(module.Course, "save", course_save)
    monkeypatch.setattr(module.CourseGenerationRun, "save", AsyncMock())

    result = await workflow.generate_outline(
        run=run,
        command_id="command:outline-replay",
        course_id="course:one",
        anchor_ids=["anchor:one"],
        available_lab_keys=[],
        model=selection,
        prompt_version="v1",
    )

    assert result.id == version.id
    assert run.status == "succeeded"
    assert course.status == "ready"
    assert course.outline_version_id == "course_version:approved"
    generate.assert_not_awaited()
    course_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_re_review_ignores_failed_partial_and_blocks_on_high_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.contracts import (
        ChapterArtifact,
        ChapterSection,
        CourseOutlineArtifact,
        ModelSelection,
        ReviewArtifact,
        ValidationFinding,
    )
    from open_notebook.course.generation_service import CourseGenerationService
    from open_notebook.course.models import (
        Chapter,
        Course,
        CourseGenerationRun,
        CourseVersion,
    )

    outline = CourseOutlineArtifact(
        title="Calculus",
        chapters=[
            {
                "key": "limits",
                "title": "Limits",
                "purpose": "Learn limits.",
                "objective_keys": ["limit"],
                "anchor_ids": ["anchor:one"],
            }
        ],
        concepts=[
            {
                "key": "limit",
                "label": "Limit",
                "anchor_ids": ["anchor:one"],
            }
        ],
    )
    artifact = ChapterArtifact(
        chapter_key="limits",
        purpose="Learn limits.",
        objectives=["Evaluate limits"],
        sections=[
            ChapterSection(
                key="definition",
                title="Definition",
                markdown="Grounded definition.",
                anchor_ids=["anchor:one"],
            )
        ],
    )
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        outline_version_id="course_version:one",
        status="generating",
    )
    version = CourseVersion(
        id="course_version:one",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=outline.model_dump(mode="json"),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        version_no=1,
        status="ready",
        review_status="passed",
        validation_status="passed",
        artifact=artifact.model_dump(mode="json"),
    )
    failed_partial = Chapter(
        id="chapter:failed-partial",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        version_no=2,
        status="reviewing",
        artifact=artifact.model_dump(mode="json"),
        input_hash="failed-partial-hash",
    )
    selection = ModelSelection(
        adapter="codex_cli", model="gpt-5.6-luna", reasoning_effort="max"
    )
    run = CourseGenerationRun(
        id="course_generation_run:re-review",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="limits",
        stage="review",
        adapter=selection.adapter,
        model=selection.model,
        reasoning_effort=selection.reasoning_effort,
        status="running",
        prompt_version="v1",
        input_hash="logical-hash",
    )
    failed_content_run = CourseGenerationRun(
        id="course_generation_run:failed-content",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:failed-partial",
        chapter_key="limits",
        stage="chapter_content",
        adapter=selection.adapter,
        model=selection.model,
        reasoning_effort=selection.reasoning_effort,
        status="failed",
        prompt_version="v1",
        input_hash="failed-content-claim",
        output_hash=None,
    )
    finding = ValidationFinding(
        kind="review",
        severity="high",
        item_key="definition",
        anchor_ids=["anchor:one"],
        message="The definition needs correction.",
    )
    generation = SimpleNamespace(
        review=AsyncMock(return_value=ReviewArtifact(findings=[finding])),
        validate_chapter=lambda _artifact, _anchors: [],
        assert_publishable=CourseGenerationService.assert_publishable,
    )
    workflow = module.CourseWorkflowService(
        generation=cast(module.CourseGenerationService, generation)
    )

    async def finding_query(statement: str, variables=None):
        variables = variables or {}
        if "FROM course_generation_run" in statement:
            return [failed_content_run.model_dump(mode="json")]
        if "FROM course_validation_finding" in statement:
            return []
        if statement.lstrip().startswith("DELETE course_validation_finding"):
            return []
        if "UPSERT $finding_id" in statement:
            return []
        if statement.lstrip().startswith("UPDATE $run_id"):
            run.status = "succeeded"
            run.output_hash = str(variables["output_hash"])
            return [run.model_dump(mode="json")]
        raise AssertionError(statement)

    monkeypatch.setattr(module.Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(
        module.CourseVersion,
        "chapters",
        AsyncMock(return_value=[chapter, failed_partial]),
    )
    monkeypatch.setattr(
        workflow, "approved_version", AsyncMock(return_value=(version, outline))
    )
    monkeypatch.setattr(
        workflow,
        "grounded_inputs",
        AsyncMock(return_value=([], {"source:one": "a" * 64}, [])),
    )
    monkeypatch.setattr(workflow, "validate_run_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "validate_run_claim", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "activate_run", AsyncMock(return_value=run))
    monkeypatch.setattr(module, "repo_query", finding_query)
    monkeypatch.setattr(module.Chapter, "save", AsyncMock())
    monkeypatch.setattr(module.CourseGenerationRun, "save", AsyncMock())

    reviewed, findings = await workflow.review_chapter(
        run=run,
        command_id="command:re-review",
        course_id="course:one",
        chapter_key="limits",
        anchor_ids=["anchor:one"],
        model=selection,
        prompt_version="v1",
    )

    assert findings == [finding]
    assert reviewed.status == "blocked"
    assert reviewed.review_status == "escalated"
    assert reviewed.validation_status == "pending"


@pytest.mark.asyncio
async def test_current_chapter_requires_key_from_complete_approved_outline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.course_command_service as module
    from open_notebook.course.contracts import CourseOutlineArtifact
    from open_notebook.course.models import Chapter, Course, CourseVersion
    from open_notebook.exceptions import NotFoundError

    outline = CourseOutlineArtifact(
        title="Calculus",
        chapters=[
            {
                "key": "limits",
                "title": "Limits",
                "purpose": "Learn limits.",
                "objective_keys": ["limit"],
                "anchor_ids": ["anchor:one"],
            }
        ],
        concepts=[
            {
                "key": "limit",
                "label": "Limit",
                "anchor_ids": ["anchor:one"],
            }
        ],
    )
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        outline_version_id="course_version:one",
    )
    outline_payload = outline.model_dump(mode="json")
    version = CourseVersion(
        id="course_version:one",
        course="course:one",
        version_no=1,
        outline_artifact=outline_payload,
        outline_hash=hashlib.sha256(
            json.dumps(
                outline_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    chapter = Chapter(
        id="chapter:invented",
        course_version="course_version:one",
        chapter_no=2,
        chapter_key="invented",
        title="Invented",
    )
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))

    with pytest.raises(NotFoundError, match="Chapter not found"):
        await module.CourseCommandService.current_chapter("course:one", "invented")


@pytest.mark.asyncio
async def test_fake_adapter_outline_approval_chapter_review_publish_replays_once(
    monkeypatch,
) -> None:
    """DB-free end-to-end proof of the V2 worker artifact path."""

    import open_notebook.course.workflow_service as module
    import open_notebook.domain.base as base_module
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
    chapter_runs: dict[str, CourseGenerationRun] = {}
    link_refreshes: list[str] = []

    async def save_course(self):
        return None

    async def save_version(self):
        if self.id is None:
            self.id = f"course_version:v{len(versions) + 1}"
        if not any(item.id == self.id for item in versions):
            versions.append(self)

    async def create_record(table, data):
        assert table == "chapter"
        row = {**data, "id": f"chapter:c{len(chapters) + 1}"}
        chapters.append(Chapter(**row))
        return row

    async def update_record(table, record_id, data):
        assert table == "chapter"
        for index, stored in enumerate(chapters):
            if str(stored.id) != str(record_id):
                continue
            row = {**stored.model_dump(mode="json"), **data, "id": str(stored.id)}
            updated = Chapter(**row)
            chapters[index] = updated
            return [updated.model_dump(mode="json")]
        # SurrealDB UPDATE of a never-created deterministic ID returns no row.
        return []

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
        if statement.lstrip().startswith("SELECT * FROM course_generation_run"):
            return [
                item.model_dump(mode="json")
                for item in chapter_runs.values()
                if item.course == str(variables["course"])
                and item.course_version == str(variables["version"])
                and item.chapter_key == variables["chapter_key"]
                and item.stage == "chapter_content"
            ]
        if statement.lstrip().startswith("UPDATE $run_id SET chapter"):
            stored_run = next(
                item
                for run_id, item in chapter_runs.items()
                if str(module.ensure_record_id(run_id))
                == str(variables["run_id"])
            )
            stored_run.chapter = str(variables["chapter_id"])
            return [stored_run.model_dump(mode="json")]
        if statement.lstrip().startswith("UPDATE $run_id") and "'succeeded'" in statement:
            stored_run = next(
                item
                for run_id, item in chapter_runs.items()
                if str(module.ensure_record_id(run_id))
                == str(variables["run_id"])
            )
            stored_run.status = "succeeded"
            stored_run.output_hash = str(variables["output_hash"])
            return [stored_run.model_dump(mode="json")]
        if statement.lstrip().startswith("BEGIN TRANSACTION"):
            stored_run = next(
                item
                for run_id, item in chapter_runs.items()
                if str(module.ensure_record_id(run_id))
                == str(variables["run_id"])
            )
            stored_run.status = "succeeded"
            stored_run.output_hash = str(variables["output_hash"])
            link_refreshes.append(str(variables["chapter_key"]))
            return []
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
    monkeypatch.setattr(module.Lab, "save", save_lab)
    monkeypatch.setattr(module.CourseGenerationRun, "save", save_run)
    monkeypatch.setattr(base_module, "repo_create", create_record)
    monkeypatch.setattr(base_module, "repo_update", update_record)
    monkeypatch.setattr(CourseService, "get_course", AsyncMock(return_value=course))
    monkeypatch.setattr("api.course_service.repo_query", query)

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
    evidence_build = AsyncMock(return_value=[anchor])
    monkeypatch.setattr(workflow.evidence, "build", evidence_build)
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
    chapter_runs[str(evidence_run.id)] = evidence_run
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
    assert evidence_build.await_count == 1
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
    chapter_runs[str(outline_run.id)] = outline_run
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
    chapter_runs[str(chapter_run.id)] = chapter_run
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
    chapter_runs[str(forced_chapter_run.id)] = forced_chapter_run
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
    chapter_runs[str(review_run.id)] = review_run
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
