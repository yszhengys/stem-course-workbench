"""Textbook assessment boundary for Course V2."""

from __future__ import annotations

from dataclasses import dataclass

from .task_backend import CourseTaskBackend


@dataclass(slots=True)
class AssessmentService:
    """Own exercise banks, difficulty, transfer checks and deterministic grading."""

    task_backend: CourseTaskBackend | None = None


__all__ = ["AssessmentService"]
