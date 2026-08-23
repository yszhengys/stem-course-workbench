"""Course V2 publication-policy boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import InvalidInputError

from .authoring_service import AuthoringService, DraftScope, DraftState

DraftLoader = Callable[[DraftScope], Awaitable[DraftState]]
RevisionQuery = Callable[[str, dict[str, Any]], Awaitable[list[Any]]]


class DraftPublicationError(InvalidInputError):
    """Raised when an edited chapter has not passed its latest local checks."""


@dataclass(slots=True)
class PublicationService:
    """Own V2 learning, evidence, and structured-draft publication gates."""

    draft_loader: DraftLoader | None = None
    revision_query: RevisionQuery = repo_query

    async def assert_draft_ready(self, scope: DraftScope) -> None:
        if self.draft_loader is None:
            rows = await self.revision_query(
                """
                SELECT id, revision_no FROM course_draft_revision
                WHERE course = $course AND course_version = $version
                  AND chapter = $chapter AND chapter_key = $chapter_key
                ORDER BY revision_no DESC LIMIT 1;
                """,
                {
                    "course": ensure_record_id(scope.course_id),
                    "version": ensure_record_id(scope.course_version_id),
                    "chapter": ensure_record_id(scope.chapter_id),
                    "chapter_key": scope.chapter_key,
                },
            )
            has_revision = any(
                (
                    isinstance(row, str)
                    and row.startswith("course_draft_revision:")
                )
                or (
                    isinstance(row, dict)
                    and str(row.get("id", "")).startswith("course_draft_revision:")
                )
                for row in rows
            )
            if not has_revision:
                return
        loader = self.draft_loader or AuthoringService().get_draft
        draft = await loader(scope)
        if draft.revision_no == 0:
            return
        if draft.revision_status != "validated":
            raise DraftPublicationError(
                "The latest structured draft revision must be validated before publication."
            )


__all__ = ["DraftPublicationError", "PublicationService"]
