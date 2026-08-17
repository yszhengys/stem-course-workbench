"""Background commands for the isolated Course workflow."""

from __future__ import annotations

from typing import Literal, NoReturn

from pydantic import ConfigDict, Field, field_validator
from surreal_commands import CommandInput, CommandOutput, command

from open_notebook.course.contracts import ModelSelection
from open_notebook.course.model_adapters import AdapterError
from open_notebook.course.workflow_service import CourseWorkflowService
from open_notebook.exceptions import (
    ConfigurationError,
    InvalidInputError,
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


COURSE_RETRY = {
    "max_attempts": 3,
    "wait_strategy": "exponential_jitter",
    "wait_min": 1,
    "wait_max": 30,
    "stop_on": [ValueError, ConfigurationError],
    "retry_log_level": "debug",
}


_workflow = CourseWorkflowService()


def _command_id(input_data: StrictCourseCommandInput) -> str | None:
    context = input_data.execution_context
    return str(context.command_id) if context else None


async def _permanent_failure(run, exc: Exception) -> None:
    await _workflow.fail_run(run, str(exc))


def _is_permanent_adapter_failure(exc: AdapterError) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "not valid json",
            "must be a json object",
            "did not match the requested schema",
            "authentication is required",
            "quota was exceeded",
            "requires a registered model id",
            "unknown course model adapter",
            "unable to start the configured codex cli",
        )
    )


async def _handle_adapter_failure(
    run, exc: AdapterError
) -> NoReturn:
    if _is_permanent_adapter_failure(exc):
        await _permanent_failure(run, exc)
        raise ValueError(str(exc)) from exc
    raise exc


@command("course_build_evidence", app="open_notebook", retry=COURSE_RETRY)
async def course_build_evidence_command(
    input_data: CourseEvidenceInput,
) -> CourseCommandOutput:
    run = await _workflow.load_run(
        run_id=input_data.run_id,
        course_id=input_data.course_id,
        stage="evidence",
        command_id=_command_id(input_data),
    )
    try:
        anchors = await _workflow.build_evidence(
            run=run,
            course_id=input_data.course_id,
            source_id=input_data.source_id,
            role=input_data.role,
        )
    except (ValueError, InvalidInputError, NotFoundError, ConfigurationError) as exc:
        await _permanent_failure(run, exc)
        raise ValueError(str(exc)) from exc
    except AdapterError as exc:
        await _handle_adapter_failure(run, exc)
    return CourseCommandOutput(
        success=True,
        run_id=input_data.run_id,
        finding_count=len(anchors),
    )


@command("course_generate_outline", app="open_notebook", retry=COURSE_RETRY)
async def course_generate_outline_command(
    input_data: CourseOutlineInput,
) -> CourseCommandOutput:
    run = await _workflow.load_run(
        run_id=input_data.run_id,
        course_id=input_data.course_id,
        stage="outline",
        command_id=_command_id(input_data),
    )
    try:
        version = await _workflow.generate_outline(
            run=run,
            course_id=input_data.course_id,
            anchor_ids=input_data.anchor_ids,
            available_lab_keys=input_data.available_lab_keys,
            model=input_data.model,
            prompt_version=input_data.prompt_version,
        )
    except (ValueError, InvalidInputError, NotFoundError, ConfigurationError) as exc:
        await _permanent_failure(run, exc)
        raise ValueError(str(exc)) from exc
    except AdapterError as exc:
        await _handle_adapter_failure(run, exc)
    return CourseCommandOutput(
        success=True, run_id=input_data.run_id, artifact_id=str(version.id)
    )


@command("course_generate_chapter", app="open_notebook", retry=COURSE_RETRY)
async def course_generate_chapter_command(
    input_data: CourseChapterInput,
) -> CourseCommandOutput:
    run = await _workflow.load_run(
        run_id=input_data.run_id,
        course_id=input_data.course_id,
        stage="chapter_content",
        command_id=_command_id(input_data),
    )
    try:
        chapter = await _workflow.generate_chapter(
            run=run,
            course_id=input_data.course_id,
            chapter_key=input_data.chapter_key,
            anchor_ids=input_data.anchor_ids,
            model=input_data.model,
            prompt_version=input_data.prompt_version,
        )
    except (ValueError, InvalidInputError, NotFoundError, ConfigurationError) as exc:
        await _permanent_failure(run, exc)
        raise ValueError(str(exc)) from exc
    except AdapterError as exc:
        await _handle_adapter_failure(run, exc)
    return CourseCommandOutput(
        success=True, run_id=input_data.run_id, artifact_id=str(chapter.id)
    )


@command("course_review_chapter", app="open_notebook", retry=COURSE_RETRY)
async def course_review_chapter_command(
    input_data: CourseChapterInput,
) -> CourseCommandOutput:
    run = await _workflow.load_run(
        run_id=input_data.run_id,
        course_id=input_data.course_id,
        stage="review",
        command_id=_command_id(input_data),
    )
    try:
        chapter, findings = await _workflow.review_chapter(
            run=run,
            course_id=input_data.course_id,
            chapter_key=input_data.chapter_key,
            anchor_ids=input_data.anchor_ids,
            model=input_data.model,
            prompt_version=input_data.prompt_version,
        )
    except (ValueError, InvalidInputError, NotFoundError, ConfigurationError) as exc:
        await _permanent_failure(run, exc)
        raise ValueError(str(exc)) from exc
    except AdapterError as exc:
        await _handle_adapter_failure(run, exc)
    return CourseCommandOutput(
        success=True,
        run_id=input_data.run_id,
        artifact_id=str(chapter.id),
        finding_count=len(findings),
    )
