"""Fail-fast registration gate for Course background commands."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from importlib import import_module
from typing import Any

from surreal_commands import registry

REQUIRED_COURSE_COMMANDS = frozenset(
    {
        "course_build_evidence",
        "course_generate_outline",
        "course_generate_chapter",
        "course_review_chapter",
    }
)


def ensure_course_commands_registered(
    *,
    importer: Callable[[str], Any] = import_module,
    registered_commands: Callable[[], Iterable[Any]] = registry.get_all_commands,
) -> None:
    """Import the complete command package and assert the Course registry surface."""

    try:
        importer("commands")
    except Exception as exc:
        raise RuntimeError("Failed to import Course commands") from exc

    try:
        registered = {
            str(item.name)
            for item in registered_commands()
            if getattr(item, "app_id", None) == "open_notebook"
        }
    except Exception as exc:
        raise RuntimeError("Failed to inspect the Course command registry") from exc

    missing = sorted(REQUIRED_COURSE_COMMANDS - registered)
    if missing:
        raise RuntimeError(
            "Missing required Course commands: " + ", ".join(missing)
        )


__all__ = ["REQUIRED_COURSE_COMMANDS", "ensure_course_commands_registered"]
