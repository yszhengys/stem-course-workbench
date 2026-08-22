"""Learning-event and mastery boundary for Course V2."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LearningService:
    """Own append-only events, mastery reduction and review scheduling."""


__all__ = ["LearningService"]
