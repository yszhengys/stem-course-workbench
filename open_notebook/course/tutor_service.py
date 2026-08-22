"""Source-grounded chapter tutor boundary for Course V2."""

from __future__ import annotations

from dataclasses import dataclass

from .task_backend import CourseTaskBackend


@dataclass(slots=True)
class TutorService:
    """Own version-bound tutor sessions and grounded response tasks."""

    task_backend: CourseTaskBackend | None = None


__all__ = ["TutorService"]
