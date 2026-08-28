"""Queue-neutral Course task interface and current surreal-commands adapter."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal, Protocol

from pydantic import (
    Field,
    FiniteFloat,
    field_serializer,
    field_validator,
    model_validator,
)
from surrealdb import RecordID

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import InvalidInputError

from .v2_contracts import Sha256, StableKey, V2Contract

CourseTaskName = Literal[
    "exercise_bank",
    "learning_recompute",
    "tutor_response",
    "draft_validate",
    "course_export",
    "course_import",
]
TaskArgumentValue = str | int | FiniteFloat | bool | None | tuple[str, ...]


class CourseTaskCancellationError(InvalidInputError):
    """Raised when the active queue backend cannot safely cancel a task."""


def _command_record_id(job_id: str) -> RecordID:
    record = ensure_record_id(job_id)
    if not str(record).startswith("command:"):
        raise InvalidInputError("job_id must be a command record ID")
    return record


def _freeze_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("task JSON values must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("task JSON object keys must be strings")
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise ValueError("task result and progress must contain JSON values")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


class CourseTaskArgument(V2Contract):
    name: StableKey
    value: TaskArgumentValue


class CourseTaskRequest(V2Contract):
    task: CourseTaskName
    idempotency_key: Sha256
    arguments: tuple[CourseTaskArgument, ...] = Field(
        default_factory=tuple, max_length=100
    )

    @model_validator(mode="after")
    def argument_names_are_unique(self) -> "CourseTaskRequest":
        names = tuple(argument.name for argument in self.arguments)
        if len(names) != len(set(names)):
            raise ValueError("task argument names must be unique")
        if "idempotency_key" in names:
            raise ValueError("idempotency_key is a reserved task argument name")
        return self

    def command_args(self) -> dict[str, TaskArgumentValue]:
        args = {argument.name: argument.value for argument in self.arguments}
        args["idempotency_key"] = self.idempotency_key
        return args


class CommandJobStatus(V2Contract):
    job_id: str = Field(pattern=r"^command:[^:]+$")
    status: Literal[
        "new",
        "queued",
        "running",
        "completed",
        "succeeded",
        "failed",
        "canceled",
        "cancelled",
        "unknown",
    ]
    result: object | None = None
    error_message: str | None = None
    created: str | None = None
    updated: str | None = None
    progress: object | None = None

    @field_validator("result", "progress", mode="before")
    @classmethod
    def opaque_json_is_deeply_immutable(cls, value: object) -> object:
        return _freeze_json(value)

    @field_serializer("result", "progress")
    def serialize_opaque_json(self, value: object) -> object:
        return _thaw_json(value)


class CourseTaskBackend(Protocol):
    async def submit(self, request: CourseTaskRequest) -> str: ...

    async def get(self, job_id: str) -> CommandJobStatus: ...

    async def cancel(self, job_id: str) -> None: ...


class SurrealCommandTaskBackend:
    """Small adapter that keeps surreal-commands out of V2 domain services."""

    def __init__(self, command_service: Any | None = None) -> None:
        if command_service is None:
            from api.command_service import CommandService

            command_service = CommandService
        self._command_service = command_service

    async def submit(self, request: CourseTaskRequest) -> str:
        job_id = await self._command_service.submit_command_job(
            "open_notebook",
            f"course_v2_{request.task}",
            request.command_args(),
        )
        return str(_command_record_id(job_id))

    async def get(self, job_id: str) -> CommandJobStatus:
        job_id = str(_command_record_id(job_id))
        raw = await self._command_service.get_command_status(job_id)
        return CommandJobStatus.model_validate(raw)

    async def cancel(self, job_id: str) -> None:
        command = _command_record_id(job_id)
        updated = await repo_query(
            """
            UPDATE $command
            SET status = 'canceled',
                error_message = 'Cancelled before execution'
            WHERE status = 'new'
            RETURN AFTER;
            """,
            {"command": command},
        )
        if updated:
            return
        status = await self.get(job_id)
        if status.status in {
            "completed",
            "succeeded",
            "failed",
            "canceled",
            "cancelled",
        }:
            return
        raise CourseTaskCancellationError(
            f"cannot safely cancel a {status.status} command with the current backend"
        )


__all__ = [
    "CommandJobStatus",
    "CourseTaskArgument",
    "CourseTaskBackend",
    "CourseTaskCancellationError",
    "CourseTaskRequest",
    "SurrealCommandTaskBackend",
]
