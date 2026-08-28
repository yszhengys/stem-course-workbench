"""Course V2 publication-policy boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import InvalidInputError

from .authoring_service import AuthoringService, DraftScope, DraftState
from .v2_models import CourseExercise

DraftLoader = Callable[[DraftScope], Awaitable[DraftState]]
RevisionQuery = Callable[[str, dict[str, Any]], Awaitable[list[Any]]]


class DraftPublicationError(InvalidInputError):
    """Raised when an edited chapter has not passed its latest local checks."""


class ExercisePublicationError(InvalidInputError):
    """Raised when the current chapter exercise bank is unsafe to publish."""


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

    async def assert_exercises_ready(self, scope: DraftScope) -> None:
        """Require one verified objective core/gating exercise with transfer."""

        rows = await self.revision_query(
            """
            SELECT * FROM course_exercise
            WHERE course = $course AND course_version = $version
              AND chapter_key = $chapter_key
            ORDER BY exercise_key;
            """,
            {
                "course": ensure_record_id(scope.course_id),
                "version": ensure_record_id(scope.course_version_id),
                "chapter_key": scope.chapter_key,
            },
        )
        try:
            exercises = tuple(
                CourseExercise(**row)
                for row in rows
                if isinstance(row, dict)
            )
        except (TypeError, ValueError) as exc:
            raise ExercisePublicationError(
                "The current chapter exercise bank is invalid."
            ) from exc
        if not exercises:
            raise ExercisePublicationError(
                "The current chapter exercise bank is required before publication."
            )
        if any(
            exercise.course != scope.course_id
            or exercise.course_version != scope.course_version_id
            or exercise.chapter_key != scope.chapter_key
            or exercise.chapter != scope.chapter_id
            for exercise in exercises
        ):
            raise ExercisePublicationError(
                "The exercise bank contains a stale chapter exercise."
            )

        cores = tuple(exercise for exercise in exercises if exercise.is_core)
        if len(cores) != 1:
            raise ExercisePublicationError(
                "The exercise bank must contain exactly one core exercise."
            )
        gates = tuple(exercise for exercise in exercises if exercise.is_gating)
        if len(gates) != 1:
            raise ExercisePublicationError(
                "The exercise bank must contain exactly one gating exercise."
            )
        core = cores[0]
        if str(core.id) != str(gates[0].id):
            raise ExercisePublicationError(
                "The core and gating exercise must be the same exercise."
            )
        if (
            core.blueprint.answer_type in {"proof", "explanation"}
            or core.grader.kind == "advisory"
        ):
            raise ExercisePublicationError(
                "The core exercise must use an objective grader."
            )
        if core.blueprint.transfer_task is None:
            raise ExercisePublicationError(
                "The core exercise must include a transfer task."
            )
        if not core.verification.mastery_eligible:
            raise ExercisePublicationError(
                "The core exercise must reach verification level L2 or L3."
            )


__all__ = [
    "DraftPublicationError",
    "ExercisePublicationError",
    "PublicationService",
]
