"""Structured draft authoring boundary for Course V2."""

from __future__ import annotations

from dataclasses import dataclass

from .task_backend import CourseTaskBackend


@dataclass(slots=True)
class AuthoringService:
    """Own draft revisions and targeted validation task submission."""

    task_backend: CourseTaskBackend | None = None


__all__ = ["AuthoringService"]
