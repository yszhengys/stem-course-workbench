"""Explicit Luna-to-Sol escalation contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from surrealdb import AsyncSurreal

from open_notebook.course.contracts import (
    ModelSelection,
    ReviewArtifact,
    ValidationFinding,
)
from open_notebook.course.generation_service import CourseGenerationService
from open_notebook.course.model_adapters import FakeCourseModelAdapter
from open_notebook.course.models import (
    Chapter,
    CourseEvidenceAnchor,
    CourseGenerationRun,
)


def _selection(model: str) -> dict[str, str]:
    return {
        "adapter": "codex_cli",
        "model": model,
        "reasoning_effort": "max",
    }


def _finding(
    item_key: str,
    *,
    severity: str = "high",
    status: str = "open",
    anchor_ids: list[str] | None = None,
) -> ValidationFinding:
    return ValidationFinding(
        kind="review",
        severity=severity,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        item_key=item_key,
        anchor_ids=anchor_ids or ["anchor:one"],
        message=f"Finding {item_key}",
    )


def test_review_contract_requires_explicit_escalation_model() -> None:
    from api.models import CourseChapterReviewRequest
    from commands.course_commands import CourseReviewInput

    api_payload = {
        "anchor_ids": ["anchor:one"],
        "prompt_version": "v1",
        "model": _selection("gpt-5.6-luna"),
        "force": False,
    }
    with pytest.raises(ValidationError, match="escalation_model"):
        CourseChapterReviewRequest.model_validate(api_payload)

    api_request = CourseChapterReviewRequest.model_validate(
        {**api_payload, "escalation_model": _selection("gpt-5.6-sol")}
    )
    assert api_request.escalation_model.model == "gpt-5.6-sol"

    worker_payload = {
        "run_id": "course_generation_run:one",
        "course_id": "course:one",
        "chapter_key": "limits",
        "anchor_ids": ["anchor:one"],
        "prompt_version": "v1",
        "model": _selection("gpt-5.6-luna"),
    }
    with pytest.raises(ValidationError, match="escalation_model"):
        CourseReviewInput.model_validate(worker_payload)

    worker = CourseReviewInput.model_validate(
        {**worker_payload, "escalation_model": _selection("gpt-5.6-sol")}
    )
    assert worker.escalation_model.model == "gpt-5.6-sol"


@pytest.mark.asyncio
async def test_raw_escalation_sends_only_eligible_findings_and_required_quotes() -> None:
    raw = ReviewArtifact(
        findings=[
            _finding(
                "high",
                status="resolved",
                anchor_ids=["anchor:one"],
            )
        ]
    )
    adapter = FakeCourseModelAdapter(raw)
    service = CourseGenerationService(adapter=adapter)
    original = [
        _finding("high", anchor_ids=["anchor:one"]),
        _finding(
            "uncertain",
            severity="warning",
            status="uncertain",
            anchor_ids=["anchor:two"],
        ),
        _finding(
            "info",
            severity="info",
            anchor_ids=["anchor:three"],
        ),
    ]

    result = await service.escalate_raw(
        course_id="course:one",
        chapter_key="limits",
        findings=original,
        evidence_by_anchor={
            "anchor:one": "quote one",
            "anchor:two": "quote two",
            "anchor:three": "must stay private from Sol",
        },
        model=ModelSelection.model_validate(_selection("gpt-5.6-sol")),
        prompt_version="v1",
    )

    assert result == raw
    assert len(adapter.calls) == 1
    call = adapter.calls[0]
    assert call.request.stage == "escalation"
    assert call.request.anchor_ids == ["anchor:one", "anchor:two"]
    assert '"item_key":"high"' in call.prompt
    assert '"item_key":"uncertain"' in call.prompt
    assert '"item_key":"info"' not in call.prompt
    assert "quote one" in call.prompt and "quote two" in call.prompt
    assert "must stay private from Sol" not in call.prompt


@pytest.mark.asyncio
async def test_review_submit_claim_carries_exact_escalation_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.course_command_service import CourseCommandService, CourseJobSubmission
    from open_notebook.course.contracts import CourseOutlineArtifact
    from open_notebook.course.models import Chapter, Course, CourseVersion

    outline = CourseOutlineArtifact.model_validate(
        {
            "title": "Calculus",
            "chapters": [
                {
                    "key": "limits",
                    "title": "Limits",
                    "purpose": "Learn limits.",
                    "objective_keys": ["limit"],
                    "anchor_ids": ["anchor:one"],
                    "lab_keys": ["limit-plot"],
                }
            ],
            "concepts": [
                {
                    "key": "limit",
                    "label": "Limit",
                    "anchor_ids": ["anchor:one"],
                }
            ],
        }
    )
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="outline_approved",
        outline_version_id="course_version:one",
    )
    version = CourseVersion(
        id="course_version:one",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=outline.model_dump(mode="json"),
        approved_at="2026-08-20T00:00:00Z",
        confirmation="确认大纲",
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        version_no=1,
        status="reviewing",
    )
    service = CourseCommandService()
    submit = AsyncMock(
        return_value=CourseJobSubmission(
            command_id="command:one",
            run_id="course_generation_run:one",
            status="queued",
        )
    )
    monkeypatch.setattr(
        service,
        "_grounded",
        AsyncMock(return_value=(course, {"source:one": "a" * 64}, [])),
    )
    monkeypatch.setattr(
        "api.course_command_service.ensure_course_models_selectable",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "open_notebook.course.workflow_service.CourseWorkflowService.approved_version",
        AsyncMock(return_value=(version, outline)),
    )
    monkeypatch.setattr(
        "open_notebook.course.models.CourseVersion.chapters",
        AsyncMock(return_value=[chapter]),
    )
    monkeypatch.setattr(
        "open_notebook.course.workflow_service.CourseWorkflowService.resolve_current_chapter",
        AsyncMock(return_value=chapter),
    )
    monkeypatch.setattr(service, "submit_stage", submit)

    review = ModelSelection.model_validate(_selection("gpt-5.6-luna"))
    escalation = ModelSelection.model_validate(_selection("gpt-5.6-sol"))
    await service.submit_review(
        course_id="course:one",
        chapter_key="limits",
        anchor_ids=["anchor:one"],
        prompt_version="v1",
        model=review,
        escalation_model=escalation,
    )

    assert submit.await_args is not None
    submitted = submit.await_args.kwargs
    assert submitted["command_args"]["escalation_model"] == escalation.model_dump(
        mode="json"
    )
    assert submitted["model"] == review


@pytest.mark.asyncio
async def test_inline_child_run_is_persistent_and_sol_replays_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import open_notebook.course.workflow_service as module

    source_hash = "a" * 64
    anchor = CourseEvidenceAnchor(
        id="course_evidence_anchor:one",
        course="course:one",
        source="source:one",
        evidence=None,
        anchor_id="anchor:one",
        locator={
            "source_id": "source:one",
            "kind": "pdf_page",
            "index": 1,
            "block_key": "block-1",
            "quote": "Grounded quote for Sol.",
            "content_sha256": source_hash,
            "bbox": None,
        },
        quote_sha256="b" * 64,
        source_role="PRIMARY",
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        version_no=1,
        status="reviewing",
    )
    parent = CourseGenerationRun(
        id="course_generation_run:parent",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="limits",
        stage="review",
        adapter="codex_cli",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        status="running",
        prompt_version="v1",
        input_hash="c" * 64,
        command="command:parent",
    )
    base = [_finding("high")]
    raw = ReviewArtifact(
        findings=[
            _finding(
                "high",
                status="resolved",
                anchor_ids=["anchor:one"],
            )
        ]
    )
    adapter = FakeCourseModelAdapter(raw)
    workflow = module.CourseWorkflowService(
        generation=CourseGenerationService(adapter=adapter)
    )
    runs: dict[str, dict[str, object]] = {}
    finding_rows: dict[str, list[dict[str, object]]] = {}

    async def query(statement: str, variables=None):
        variables = variables or {}
        if statement.lstrip().startswith("BEGIN TRANSACTION"):
            run_id = str(variables["run"])
            finding_rows[run_id] = [
                {
                    "id": str(item["id"]),
                    "generation_run": run_id,
                    **dict(item["content"]),
                }
                for item in variables["findings"]
            ]
            if "output_hash" in variables:
                runs[run_id]["status"] = "succeeded"
                runs[run_id]["output_hash"] = variables["output_hash"]
            return []
        if "FROM course_generation_run WHERE input_hash" in statement:
            return [
                dict(row)
                for row in runs.values()
                if row["input_hash"] == variables["input_hash"]
            ]
        if statement.lstrip().startswith("CREATE ONLY $run_id"):
            run_id = str(variables["run_id"])
            row = {"id": run_id, **dict(variables["payload"])}
            runs[run_id] = row
            return [dict(row)]
        if statement.lstrip().startswith("UPDATE $run_id SET status = 'running'"):
            run_id = str(variables["run_id"])
            runs[run_id]["status"] = "running"
            return [dict(runs[run_id])]
        if statement.lstrip().startswith("DELETE course_validation_finding"):
            finding_rows[str(variables["run"])] = []
            return []
        if "UPSERT $finding_id" in statement:
            run_id = str(variables["run"])
            finding_rows.setdefault(run_id, []).append(
                {
                    "id": str(variables["finding_id"]),
                    "generation_run": run_id,
                    "finding": dict(variables["finding"]),
                }
            )
            return []
        if "FROM course_validation_finding" in statement:
            return list(finding_rows.get(str(variables["run"]), []))
        if statement.lstrip().startswith("UPDATE $run_id") and "output_hash" in statement:
            run_id = str(variables["run_id"])
            runs[run_id]["status"] = "succeeded"
            runs[run_id]["output_hash"] = variables["output_hash"]
            return [dict(runs[run_id])]
        raise AssertionError(statement)

    monkeypatch.setattr(module, "repo_query", query)
    selection = ModelSelection.model_validate(_selection("gpt-5.6-sol"))

    first = await workflow._inline_escalation(
        parent_run=parent,
        course_id="course:one",
        version_id="course_version:one",
        chapter=chapter,
        findings=base,
        selected_anchors=[anchor],
        source_hashes={"source:one": source_hash},
        model=selection,
        prompt_version="v1",
    )
    second = await workflow._inline_escalation(
        parent_run=parent,
        course_id="course:one",
        version_id="course_version:one",
        chapter=chapter,
        findings=base,
        selected_anchors=[anchor],
        source_hashes={"source:one": source_hash},
        model=selection,
        prompt_version="v1",
    )

    assert len(runs) == 1
    child = CourseGenerationRun.model_validate(next(iter(runs.values())))
    assert child.stage == "escalation"
    assert child.command is None
    assert child.status == "succeeded"
    assert child.adapter == "codex_cli"
    assert child.model == "gpt-5.6-sol"
    assert len(adapter.calls) == 1
    assert first[0].status == second[0].status == "resolved"


@pytest.mark.asyncio
async def test_finding_output_and_run_terminalization_are_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed terminal CAS must not leave a partial audit checkpoint."""

    import open_notebook.course.models as models_module
    import open_notebook.course.workflow_service as module
    from open_notebook.database import repository

    database = AsyncSurreal("mem://")
    await database.use("course_escalation_test", "course_escalation_test")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    monkeypatch.setattr(module, "repo_query", repository.repo_query)
    monkeypatch.setattr(models_module, "repo_query", repository.repo_query)
    await repository.repo_query(
        """
        CREATE course_generation_run:child SET
            course = course:one,
            course_version = course_version:one,
            chapter = chapter:one,
            chapter_key = 'limits',
            stage = 'escalation',
            adapter = 'codex_cli',
            model = 'gpt-5.6-sol',
            reasoning_effort = 'max',
            status = 'failed',
            prompt_version = 'v1',
            input_hash = $input_hash,
            output_hash = NONE,
            command = NONE;
        """,
        {"input_hash": "a" * 64},
    )
    run = CourseGenerationRun(
        id="course_generation_run:child",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="limits",
        stage="escalation",
        adapter="codex_cli",
        model="gpt-5.6-sol",
        reasoning_effort="max",
        status="running",
        prompt_version="v1",
        input_hash="a" * 64,
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        version_no=1,
        status="reviewing",
    )
    raw = [_finding("high", status="resolved")]

    with pytest.raises(ValueError, match="no longer active"):
        await module.CourseWorkflowService._persist_findings(
            run=run,
            course_id="course:one",
            version_id="course_version:one",
            chapter=chapter,
            findings=raw,
            completion_output=module._canonical_escalation_output(run, raw),
        )

    assert await repository.repo_query(
        "SELECT * FROM course_validation_finding;"
    ) == []
    await database.close()


@pytest.mark.asyncio
async def test_worker_rejects_unavailable_escalation_before_review_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import commands.course_commands as module

    request = module.CourseReviewInput.model_validate(
        {
            "run_id": "course_generation_run:one",
            "course_id": "course:one",
            "chapter_key": "limits",
            "anchor_ids": ["anchor:one"],
            "prompt_version": "v1",
            "model": _selection("gpt-5.6-luna"),
            "escalation_model": _selection("gpt-5.6-sol"),
            "execution_context": {
                "command_id": "command:one",
                "execution_started_at": "2026-08-20T00:00:00Z",
                "app_name": "open_notebook",
                "command_name": "course_review_chapter",
            },
        }
    )
    availability = AsyncMock(side_effect=ValueError("model unavailable"))
    permanent = AsyncMock()
    review = AsyncMock()
    monkeypatch.setattr(module, "ensure_course_models_selectable", availability)
    monkeypatch.setattr(module, "_permanent_failure", permanent)
    monkeypatch.setattr(module._workflow, "review_chapter", review)

    with pytest.raises(ValueError, match="model unavailable"):
        await module.course_review_chapter_command(request)

    availability.assert_awaited_once_with(
        [request.model, request.escalation_model]
    )
    review.assert_not_awaited()
    permanent.assert_awaited_once()


@pytest.mark.asyncio
async def test_authoritative_findings_exclude_escalation_and_historical_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.models import CourseValidationFinding

    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        version_no=1,
        status="blocked",
    )
    current_artifact = _finding("current", status="resolved")
    historical_artifact = _finding("historical")
    child_artifact = _finding("child", status="resolved")

    def run(record_id: str, stage: str, finding: ValidationFinding) -> CourseGenerationRun:
        item = CourseGenerationRun(
            id=record_id,
            course="course:one",
            course_version="course_version:one",
            chapter="chapter:one",
            chapter_key="limits",
            stage=stage,
            adapter="codex_cli",
            model="gpt-5.6-sol" if stage == "escalation" else "gpt-5.6-luna",
            reasoning_effort="max",
            status="succeeded",
            prompt_version="v1",
            input_hash=record_id.rsplit(":", 1)[-1].ljust(64, "0")[:64],
        )
        canonical = (
            module._canonical_escalation_output(item, [finding])
            if stage == "escalation"
            else module._canonical_review_output(item, [finding])
        )
        item.output_hash = module._artifact_hash({"output": canonical})
        return item

    current = run("course_generation_run:current", "review", current_artifact)
    historical = run("course_generation_run:historical", "review", historical_artifact)
    child = run("course_generation_run:child", "escalation", child_artifact)
    records = {
        str(current.id): CourseValidationFinding(
            id="course_validation_finding:current",
            course="course:one",
            course_version="course_version:one",
            chapter="chapter:one",
            generation_run=str(current.id),
            chapter_key="limits",
            finding=current_artifact.model_copy(
                update={"reviewer_run_id": str(current.id)}
            ).model_dump(mode="json"),
            severity=current_artifact.severity,
            status=current_artifact.status,
        ),
        str(historical.id): CourseValidationFinding(
            id="course_validation_finding:historical",
            course="course:one",
            course_version="course_version:one",
            chapter="chapter:one",
            generation_run=str(historical.id),
            chapter_key="limits",
            finding=historical_artifact.model_copy(
                update={"reviewer_run_id": str(historical.id)}
            ).model_dump(mode="json"),
            severity=historical_artifact.severity,
            status=historical_artifact.status,
        ),
        str(child.id): CourseValidationFinding(
            id="course_validation_finding:child",
            course="course:one",
            course_version="course_version:one",
            chapter="chapter:one",
            generation_run=str(child.id),
            chapter_key="limits",
            finding=child_artifact.model_copy(
                update={"reviewer_run_id": str(child.id)}
            ).model_dump(mode="json"),
            severity=child_artifact.severity,
            status=child_artifact.status,
        ),
    }

    async def query(statement: str, variables=None):
        variables = variables or {}
        if "FROM course_generation_run" in statement:
            # Deliberately include both history and audit child. The resolver
            # must use only the newest successful parent review run.
            return [
                child.model_dump(mode="json"),
                current.model_dump(mode="json"),
                historical.model_dump(mode="json"),
            ]
        if "FROM course_validation_finding" in statement:
            record = records.get(str(variables["run"]))
            return [record.model_dump(mode="json")] if record else []
        raise AssertionError(statement)

    monkeypatch.setattr(module, "repo_query", query)
    selected_run, selected = await module.CourseWorkflowService.authoritative_review_findings(
        course_id="course:one",
        version_id="course_version:one",
        chapter=chapter,
    )

    assert selected_run is not None
    assert selected_run.id == current.id
    assert [str(item.id) for item in selected] == [
        "course_validation_finding:current"
    ]


@pytest.mark.asyncio
async def test_failed_latest_review_does_not_reactivate_historical_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.models import CourseValidationFinding

    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        version_no=1,
        status="blocked",
    )
    historical_finding = _finding("historical")
    historical = CourseGenerationRun(
        id="course_generation_run:historical",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="limits",
        stage="review",
        adapter="codex_cli",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        status="succeeded",
        prompt_version="v1",
        input_hash="a" * 64,
    )
    historical.output_hash = module._artifact_hash(
        {
            "output": module._canonical_review_output(
                historical, [historical_finding]
            )
        }
    )
    latest = historical.model_copy(
        update={
            "id": "course_generation_run:latest",
            "status": "failed",
            "input_hash": "b" * 64,
            "output_hash": None,
        }
    )
    record = CourseValidationFinding(
        id="course_validation_finding:historical",
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        generation_run=str(historical.id),
        chapter_key="limits",
        finding=historical_finding.model_copy(
            update={"reviewer_run_id": str(historical.id)}
        ).model_dump(mode="json"),
        severity="high",
        status="open",
    )

    async def query(statement: str, variables=None):
        variables = variables or {}
        if "FROM course_generation_run" in statement:
            return [
                latest.model_dump(mode="json"),
                historical.model_dump(mode="json"),
            ]
        if "FROM course_validation_finding" in statement:
            return (
                [record.model_dump(mode="json")]
                if str(variables["run"]) == str(historical.id)
                else []
            )
        raise AssertionError(statement)

    monkeypatch.setattr(module, "repo_query", query)
    selected_run, selected = (
        await module.CourseWorkflowService.authoritative_review_findings(
            course_id="course:one",
            version_id="course_version:one",
            chapter=chapter,
        )
    )

    assert selected_run is None
    assert selected == []


@pytest.mark.asyncio
async def test_sol_failure_blocks_chapter_and_fails_review_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import open_notebook.course.workflow_service as module
    from open_notebook.course.contracts import ChapterArtifact, CourseOutlineArtifact
    from open_notebook.course.models import Course, CourseVersion

    source_hash = "a" * 64
    anchor = CourseEvidenceAnchor(
        id="course_evidence_anchor:one",
        course="course:one",
        source="source:one",
        evidence=None,
        anchor_id="anchor:one",
        locator={
            "source_id": "source:one",
            "kind": "pdf_page",
            "index": 1,
            "block_key": "block-1",
            "quote": "Grounded quote.",
            "content_sha256": source_hash,
            "bbox": None,
        },
        quote_sha256="b" * 64,
        source_role="PRIMARY",
    )
    outline = CourseOutlineArtifact.model_validate(
        {
            "title": "Calculus",
            "chapters": [{
                "key": "limits", "title": "Limits", "purpose": "Learn limits.",
                "objective_keys": ["limit"], "anchor_ids": ["anchor:one"],
                "lab_keys": ["limit-plot"],
            }],
            "concepts": [{
                "key": "limit", "label": "Limit", "anchor_ids": ["anchor:one"],
            }],
        }
    )
    course = Course(
        id="course:one", title="Calculus", notebook="notebook:one",
        status="generating", outline_version_id="course_version:one",
    )
    version = CourseVersion(
        id="course_version:one", course="course:one", version_no=1,
        status="generating", outline_artifact=outline.model_dump(mode="json"),
        approved_at="2026-08-20T00:00:00Z", confirmation="确认大纲",
    )
    artifact = ChapterArtifact.model_validate({
        "chapter_key": "limits",
        "purpose": "Learn limits.",
        "objectives": ["Understand limits"],
        "sections": [{
            "key": "definition", "title": "Definition", "markdown": "Grounded.",
            "anchor_ids": ["anchor:one"], "provenance": "adapted",
        }],
        "attributions": {
            "purpose": {"anchor_ids": ["anchor:one"], "provenance": "adapted"},
            "prerequisites": [],
            "objectives": [{"anchor_ids": ["anchor:one"], "provenance": "adapted"}],
            "definitions": [], "misconceptions": [], "pitfalls": [], "quick_reference": [],
        },
    })
    chapter = Chapter(
        id="chapter:one", course_version="course_version:one", chapter_no=1,
        chapter_key="limits", title="Limits", version_no=1, status="reviewing",
        artifact=artifact.model_dump(mode="json"),
    )
    review_selection = ModelSelection.model_validate(_selection("gpt-5.6-luna"))
    escalation_selection = ModelSelection.model_validate(_selection("gpt-5.6-sol"))
    run = CourseGenerationRun(
        id="course_generation_run:review", course="course:one",
        course_version="course_version:one", chapter="chapter:one",
        chapter_key="limits", stage="review", adapter=review_selection.adapter,
        model=review_selection.model, reasoning_effort="max", status="running",
        prompt_version="v1", input_hash="c" * 64, command="command:review",
    )
    high = _finding("definition")
    generation = SimpleNamespace(
        review=AsyncMock(return_value=ReviewArtifact(findings=[high])),
        validate_chapter=lambda _artifact, _anchors, *, subject=None: [],
        requires_escalation=CourseGenerationService.requires_escalation,
        assert_publishable=CourseGenerationService.assert_publishable,
    )
    workflow = module.CourseWorkflowService(generation=generation)  # type: ignore[arg-type]
    persisted: list[dict[str, object]] = []

    async def query(statement: str, variables=None):
        variables = variables or {}
        if statement.lstrip().startswith("BEGIN TRANSACTION"):
            persisted[:] = [
                {"finding": dict(item["content"]["finding"])}
                for item in variables["findings"]
            ]
            return []
        if "FROM course_validation_finding" in statement:
            return list(persisted)
        if statement.lstrip().startswith("DELETE course_validation_finding"):
            persisted.clear()
            return []
        if "UPSERT $finding_id" in statement:
            persisted.append({"finding": dict(variables["finding"])})
            return []
        raise AssertionError(statement)

    monkeypatch.setattr(module, "repo_query", query)
    monkeypatch.setattr(module.Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(workflow, "approved_version", AsyncMock(return_value=(version, outline)))
    monkeypatch.setattr(
        workflow, "grounded_inputs",
        AsyncMock(return_value=([anchor], {"source:one": source_hash}, [])),
    )
    monkeypatch.setattr(workflow, "validate_run_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "validate_run_claim", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "activate_run", AsyncMock(return_value=run))
    monkeypatch.setattr(workflow, "resolve_current_chapter", AsyncMock(return_value=chapter))
    monkeypatch.setattr(workflow, "_inline_escalation", AsyncMock(side_effect=RuntimeError("Sol failed")))
    monkeypatch.setattr(module.CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(module.Chapter, "save", AsyncMock())

    with pytest.raises(RuntimeError, match="Sol failed"):
        await workflow.review_chapter(
            run=run,
            command_id="command:review",
            course_id="course:one",
            chapter_key="limits",
            anchor_ids=["anchor:one"],
            model=review_selection,
            escalation_model=escalation_selection,
            prompt_version="v1",
        )

    assert chapter.status == "blocked"
    assert chapter.review_status == "failed"
    assert chapter.validation_status == "failed"


@pytest.mark.asyncio
async def test_escalation_audit_finding_cannot_be_patched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.course_command_service import CourseCommandService
    from open_notebook.course.models import CourseValidationFinding
    from open_notebook.exceptions import NotFoundError

    chapter = Chapter(
        id="chapter:one", course_version="course_version:one", chapter_no=1,
        chapter_key="limits", title="Limits", version_no=1, status="blocked",
    )
    child_finding = CourseValidationFinding(
        id="course_validation_finding:child", course="course:one",
        course_version="course_version:one", chapter="chapter:one",
        generation_run="course_generation_run:child", chapter_key="limits",
        finding=_finding("child", status="resolved").model_dump(mode="json"),
        severity="high", status="resolved",
    )
    parent_finding = child_finding.model_copy(
        update={
            "id": "course_validation_finding:parent",
            "generation_run": "course_generation_run:parent",
        }
    )
    save = AsyncMock()
    monkeypatch.setattr(CourseValidationFinding, "get", AsyncMock(return_value=child_finding))
    monkeypatch.setattr(CourseValidationFinding, "save", save)
    monkeypatch.setattr(
        CourseCommandService, "current_chapter", AsyncMock(return_value=chapter)
    )
    monkeypatch.setattr(
        "api.course_command_service.CourseWorkflowService.authoritative_review_findings",
        AsyncMock(return_value=(None, [parent_finding])),
    )

    with pytest.raises(NotFoundError, match="Validation finding"):
        await CourseCommandService.update_finding(
            course_id="course:one",
            finding_id="course_validation_finding:child",
            status="resolved",
            resolution_reason="Attempted audit edit.",
        )

    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_availability_gate_is_explicit_and_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import open_notebook.course.model_adapters as module
    from open_notebook.course.model_adapters import AdapterError

    probe = AsyncMock()
    monkeypatch.delenv("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS", raising=False)
    monkeypatch.setattr(module.Model, "get_models_by_type", probe)
    with pytest.raises(AdapterError, match="disabled"):
        await module.ensure_course_models_selectable(
            [ModelSelection.model_validate(_selection("gpt-5.6-sol"))]
        )
    probe.assert_not_awaited()

    monkeypatch.setenv("OPEN_NOTEBOOK_COURSE_ALLOW_REAL_MODELS", "1")
    monkeypatch.setattr(module.CodexCliAdapter, "is_available", lambda: True)
    with pytest.raises(AdapterError, match="unavailable"):
        await module.ensure_course_models_selectable(
            [ModelSelection.model_validate(_selection("gpt-5.6-unknown"))]
        )


def test_parent_review_claim_hash_changes_with_escalation_selection() -> None:
    from open_notebook.course.workflow_service import generation_input_hash

    review = ModelSelection.model_validate(_selection("gpt-5.6-luna"))
    def command_args(escalation_model: str) -> dict[str, object]:
        return {
            "course_id": "course:one",
            "chapter_key": "limits",
            "anchor_ids": ["anchor:one"],
            "prompt_version": "v1",
            "model": review.model_dump(mode="json"),
            "escalation_model": _selection(escalation_model),
        }

    def digest(escalation_model: str) -> str:
        return generation_input_hash(
            course_id="course:one",
            stage="review",
            command_args=command_args(escalation_model),
            model=review,
            prompt_version="v1",
            anchor_ids=["anchor:one"],
            source_hashes={"source:one": "a" * 64},
            course_version_id="course_version:one",
            chapter_id="chapter:one",
            chapter_key="limits",
        )

    assert digest("gpt-5.6-sol") != digest("gpt-5.6-luna")
