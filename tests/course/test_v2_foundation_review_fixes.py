from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

import open_notebook.course.task_backend as task_backend_module
from open_notebook.course.task_backend import (
    CommandJobStatus,
    CourseTaskArgument,
    CourseTaskCancellationError,
    CourseTaskRequest,
    SurrealCommandTaskBackend,
)
from open_notebook.course.v2_contracts import (
    DraftRevision,
    ReplaceLabOperation,
    ReplaceTextOperation,
)
from open_notebook.course.v2_models import CourseTutorTurn


def valid_lab() -> dict[str, object]:
    return {
        "kind": "function_plot",
        "key": "limit-plot",
        "title": "Limit plot",
        "expressions": ["x"],
        "domain": {"x": (-1.0, 1.0)},
        "controls": [],
        "objects": [],
        "anchor_ids": ["anchor:one"],
        "provenance": "adapted",
    }


def test_replace_lab_operation_owns_an_immutable_validated_snapshot() -> None:
    source = valid_lab()
    operation = ReplaceLabOperation(
        kind="replace_lab", block_key="lab-1", lab_spec=source
    )
    cast(list[str], source["expressions"]).append("mutated")

    dumped = operation.model_dump(mode="json")
    assert dumped["lab_spec"]["expressions"] == ["x"]
    with pytest.raises(ValidationError, match="frozen"):
        operation.lab_spec.root = "{}"  # type: ignore[misc]


def test_command_status_freezes_opaque_json_without_changing_wire_shape() -> None:
    raw = {
        "job_id": "command:one",
        "status": "running",
        "result": {"items": [1, {"ok": True}]},
        "error_message": None,
        "created": "2026-08-21T00:00:00Z",
        "updated": "2026-08-21T00:01:00Z",
        "progress": {"completed": 1},
    }
    status = CommandJobStatus.model_validate(raw)

    with pytest.raises(TypeError):
        cast(dict[str, Any], status.progress)["completed"] = 2
    with pytest.raises(AttributeError):
        cast(list[object], cast(dict[str, Any], status.result)["items"]).append(2)
    assert status.model_dump(mode="json") == raw


def test_tutor_refusal_reason_survives_the_persistent_record_contract() -> None:
    refusal = CourseTutorTurn(
        course="course:one",
        course_version="course_version:one",
        session="course_tutor_session:one",
        chapter_key="limits",
        turn_no=1,
        role="assistant",
        content="The selected evidence is insufficient.",
        anchor_ids=[],
        insufficient_evidence=True,
    )
    assert refusal._prepare_save_data()["insufficient_evidence"] is True

    with pytest.raises(ValidationError, match="evidence anchors"):
        CourseTutorTurn(
            course="course:one",
            course_version="course_version:one",
            session="course_tutor_session:one",
            chapter_key="limits",
            turn_no=2,
            role="assistant",
            content="An uncited factual answer.",
            anchor_ids=[],
            insufficient_evidence=False,
        )


def test_task_arguments_are_unique_and_draft_checks_are_closed() -> None:
    with pytest.raises(ValidationError, match="argument names"):
        CourseTaskRequest(
            task="exercise_bank",
            idempotency_key="a" * 64,
            arguments=(
                CourseTaskArgument(name="course_id", value="course:one"),
                CourseTaskArgument(name="course_id", value="course:two"),
            ),
        )

    with pytest.raises(ValidationError):
        DraftRevision(
            revision_no=1,
            base_artifact_hash="b" * 64,
            artifact_hash="c" * 64,
            operation=ReplaceTextOperation(
                kind="replace_text", block_key="purpose", text="Safe text"
            ),
            invalidated_checks=("arbitrary",),
            created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_backend_cancels_only_a_still_queued_command(monkeypatch) -> None:
    update = AsyncMock(return_value=[{"id": "command:one", "status": "canceled"}])
    monkeypatch.setattr(task_backend_module, "repo_query", update)
    service = AsyncMock()
    backend = SurrealCommandTaskBackend(command_service=service)

    await backend.cancel("command:one")

    assert update.await_args is not None
    assert "status = 'new'" in update.await_args.args[0]
    service.get_command_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_backend_refuses_to_fake_running_command_cancellation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        task_backend_module, "repo_query", AsyncMock(return_value=[])
    )
    service = AsyncMock()
    service.get_command_status.return_value = {
        "job_id": "command:one",
        "status": "running",
        "result": None,
        "error_message": None,
        "created": None,
        "updated": None,
        "progress": None,
    }
    backend = SurrealCommandTaskBackend(command_service=service)

    with pytest.raises(CourseTaskCancellationError, match="running"):
        await backend.cancel("command:one")
