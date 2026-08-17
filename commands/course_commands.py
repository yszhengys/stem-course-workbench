"""Background commands for the isolated Course workflow."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Literal, NoReturn, TypeVar

import httpx
from pydantic import ConfigDict, Field, field_validator
from surreal_commands import CommandInput, CommandOutput, command

from open_notebook.course.contracts import ModelSelection
from open_notebook.course.model_adapters import AdapterError
from open_notebook.course.workflow_service import CourseWorkflowService
from open_notebook.exceptions import (
    ConfigurationError,
    InvalidInputError,
    NetworkError,
    NotFoundError,
)


class StrictCourseCommandInput(CommandInput):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class AnchoredCourseCommandInput(StrictCourseCommandInput):
    run_id: str = Field(pattern=r"^course_generation_run:")
    course_id: str = Field(pattern=r"^course:")
    anchor_ids: list[str] = Field(min_length=1, max_length=500)
    prompt_version: str = Field(min_length=1, max_length=100)
    model: ModelSelection

    @field_validator("anchor_ids")
    @classmethod
    def anchors_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("anchor IDs must be unique")
        return value


class CourseEvidenceInput(StrictCourseCommandInput):
    run_id: str = Field(pattern=r"^course_generation_run:")
    course_id: str = Field(pattern=r"^course:")
    source_id: str = Field(pattern=r"^source:")
    role: Literal["PRIMARY", "SUPPLEMENT"]


class CourseOutlineInput(AnchoredCourseCommandInput):
    available_lab_keys: list[str] = Field(max_length=100)


class CourseChapterInput(AnchoredCourseCommandInput):
    chapter_key: str = Field(min_length=1, max_length=100)


class CourseCommandOutput(CommandOutput):
    success: bool
    run_id: str
    artifact_id: str | None = None
    finding_count: int = 0


COURSE_ADAPTER_MAX_ATTEMPTS = 3
COURSE_ADAPTER_WAIT_MAX_SECONDS = 30
COURSE_RETRY = {
    "max_attempts": COURSE_ADAPTER_MAX_ATTEMPTS,
    "wait_strategy": "exponential_jitter",
    "wait_min": 1,
    "wait_max": COURSE_ADAPTER_WAIT_MAX_SECONDS,
    "stop_on": [ValueError, ConfigurationError],
    "retry_log_level": "debug",
}


_workflow = CourseWorkflowService()
OperationResultT = TypeVar("OperationResultT")


class AdapterFailureDisposition(str, Enum):
    """Worker retry decision derived only from machine-readable exception types."""

    PERMANENT = "permanent"
    TRANSIENT = "transient"


class _TerminalAdapterFailure(ValueError):
    """Adapter failure already synchronized to its persistent Course run."""


def _command_id(input_data: StrictCourseCommandInput) -> str | None:
    context = input_data.execution_context
    return str(context.command_id) if context else None


async def _permanent_failure(
    input_data: StrictCourseCommandInput, exc: Exception
) -> None:
    await _workflow.fail_run_reference(
        run_id=input_data.run_id,
        command_id=_command_id(input_data) or "",
        message=str(exc),
    )


def _adapter_failure_disposition(exc: AdapterError) -> AdapterFailureDisposition:
    """Retry only failures with an explicitly typed transient cause chain."""

    cause = exc.__cause__
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(
            cause,
            (
                TimeoutError,
                ConnectionError,
                NetworkError,
                httpx.TimeoutException,
                httpx.NetworkError,
            ),
        ):
            return AdapterFailureDisposition.TRANSIENT
        cause = cause.__cause__
    return AdapterFailureDisposition.PERMANENT


async def _handle_adapter_failure(
    input_data: StrictCourseCommandInput, exc: AdapterError
) -> NoReturn:
    if _adapter_failure_disposition(exc) is AdapterFailureDisposition.PERMANENT:
        await _permanent_failure(input_data, exc)
        raise _TerminalAdapterFailure(str(exc)) from exc
    raise exc


async def _execute_course_operation(
    input_data: StrictCourseCommandInput,
    operation: Callable[[], Awaitable[OperationResultT]],
) -> OperationResultT:
    """Retry only typed transient adapter failures and terminalize exhaustion."""

    max_attempts = COURSE_ADAPTER_MAX_ATTEMPTS
    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except AdapterError as exc:
            if _adapter_failure_disposition(exc) is AdapterFailureDisposition.PERMANENT:
                await _handle_adapter_failure(input_data, exc)
            if attempt == max_attempts:
                await _permanent_failure(input_data, exc)
                raise _TerminalAdapterFailure(str(exc)) from exc
            await asyncio.sleep(
                min(2 ** (attempt - 1), COURSE_ADAPTER_WAIT_MAX_SECONDS)
            )
    raise AssertionError("unreachable adapter retry state")


@command("course_build_evidence", app="open_notebook", retry=COURSE_RETRY)
async def course_build_evidence_command(
    input_data: CourseEvidenceInput,
) -> CourseCommandOutput:
    command_id = _command_id(input_data)

    async def operation():
        run = await _workflow.load_run(
            run_id=input_data.run_id,
            course_id=input_data.course_id,
            stage="evidence",
            command_id=command_id,
        )
        return await _workflow.build_evidence(
            run=run,
            command_id=command_id or "",
            course_id=input_data.course_id,
            source_id=input_data.source_id,
            role=input_data.role,
        )

    try:
        anchors = await _execute_course_operation(input_data, operation)
    except _TerminalAdapterFailure:
        raise
    except (ValueError, InvalidInputError, NotFoundError, ConfigurationError) as exc:
        await _permanent_failure(input_data, exc)
        raise ValueError(str(exc)) from exc
    return CourseCommandOutput(
        success=True,
        run_id=input_data.run_id,
        finding_count=len(anchors),
    )


@command("course_generate_outline", app="open_notebook", retry=COURSE_RETRY)
async def course_generate_outline_command(
    input_data: CourseOutlineInput,
) -> CourseCommandOutput:
    command_id = _command_id(input_data)

    async def operation():
        run = await _workflow.load_run(
            run_id=input_data.run_id,
            course_id=input_data.course_id,
            stage="outline",
            command_id=command_id,
        )
        return await _workflow.generate_outline(
            run=run,
            command_id=command_id or "",
            course_id=input_data.course_id,
            anchor_ids=input_data.anchor_ids,
            available_lab_keys=input_data.available_lab_keys,
            model=input_data.model,
            prompt_version=input_data.prompt_version,
        )

    try:
        version = await _execute_course_operation(input_data, operation)
    except _TerminalAdapterFailure:
        raise
    except (ValueError, InvalidInputError, NotFoundError, ConfigurationError) as exc:
        await _permanent_failure(input_data, exc)
        raise ValueError(str(exc)) from exc
    return CourseCommandOutput(
        success=True, run_id=input_data.run_id, artifact_id=str(version.id)
    )


@command("course_generate_chapter", app="open_notebook", retry=COURSE_RETRY)
async def course_generate_chapter_command(
    input_data: CourseChapterInput,
) -> CourseCommandOutput:
    command_id = _command_id(input_data)

    async def operation():
        run = await _workflow.load_run(
            run_id=input_data.run_id,
            course_id=input_data.course_id,
            stage="chapter_content",
            command_id=command_id,
        )
        return await _workflow.generate_chapter(
            run=run,
            command_id=command_id or "",
            course_id=input_data.course_id,
            chapter_key=input_data.chapter_key,
            anchor_ids=input_data.anchor_ids,
            model=input_data.model,
            prompt_version=input_data.prompt_version,
        )

    try:
        chapter = await _execute_course_operation(input_data, operation)
    except _TerminalAdapterFailure:
        raise
    except (ValueError, InvalidInputError, NotFoundError, ConfigurationError) as exc:
        await _permanent_failure(input_data, exc)
        raise ValueError(str(exc)) from exc
    return CourseCommandOutput(
        success=True, run_id=input_data.run_id, artifact_id=str(chapter.id)
    )


@command("course_review_chapter", app="open_notebook", retry=COURSE_RETRY)
async def course_review_chapter_command(
    input_data: CourseChapterInput,
) -> CourseCommandOutput:
    command_id = _command_id(input_data)

    async def operation():
        run = await _workflow.load_run(
            run_id=input_data.run_id,
            course_id=input_data.course_id,
            stage="review",
            command_id=command_id,
        )
        return await _workflow.review_chapter(
            run=run,
            command_id=command_id or "",
            course_id=input_data.course_id,
            chapter_key=input_data.chapter_key,
            anchor_ids=input_data.anchor_ids,
            model=input_data.model,
            prompt_version=input_data.prompt_version,
        )

    try:
        chapter, findings = await _execute_course_operation(input_data, operation)
    except _TerminalAdapterFailure:
        raise
    except (ValueError, InvalidInputError, NotFoundError, ConfigurationError) as exc:
        await _permanent_failure(input_data, exc)
        raise ValueError(str(exc)) from exc
    return CourseCommandOutput(
        success=True,
        run_id=input_data.run_id,
        artifact_id=str(chapter.id),
        finding_count=len(findings),
    )
