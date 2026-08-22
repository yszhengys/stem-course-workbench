"""Course V2 publication-policy boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PublicationService:
    """Own V2 learning, evidence and draft publication gates."""


__all__ = ["PublicationService"]
