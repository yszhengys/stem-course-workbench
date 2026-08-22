"""Manual .stemcourse portability boundary for Course V2."""

from __future__ import annotations

from dataclasses import dataclass

from .task_backend import CourseTaskBackend


@dataclass(slots=True)
class PortabilityService:
    """Own manifest-checked manual export and transactional import tasks."""

    task_backend: CourseTaskBackend | None = None


__all__ = ["PortabilityService"]
