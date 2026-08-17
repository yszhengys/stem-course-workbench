"""Course module state machines (PDR-003).

The transition tables here are the *contract*: routers and generation jobs
must go through `transition()` — direct field writes that bypass these rules
are bugs. Transitions are deliberately small and explicit so the API surface
stays auditable.
"""

import unicodedata
from typing import Any, Dict, FrozenSet

from open_notebook.exceptions import InvalidInputError


class CourseStatus:
    DRAFT = "draft"
    INDEXING = "indexing"
    OUTLINE_READY = "outline_ready"
    OUTLINE_APPROVED = "outline_approved"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"

    ALL: FrozenSet[str] = frozenset(
        {DRAFT, INDEXING, OUTLINE_READY, OUTLINE_APPROVED, GENERATING, READY, FAILED}
    )


class VersionStatus:
    DRAFT = "draft"
    GENERATING = "generating"
    PUBLISHED = "published"
    FAILED = "failed"

    ALL: FrozenSet[str] = frozenset({DRAFT, GENERATING, PUBLISHED, FAILED})


class ChapterReviewStatus:
    PENDING = "pending"
    PASSED = "passed"
    ESCALATED = "escalated"
    FAILED = "failed"

    ALL: FrozenSet[str] = frozenset({PENDING, PASSED, ESCALATED, FAILED})


class ChapterValidationStatus:
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"

    ALL: FrozenSet[str] = frozenset({PENDING, PASSED, FAILED})


class EvidenceStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"

    ALL: FrozenSet[str] = frozenset({PENDING, PROCESSING, READY, FAILED})


class AttemptStatus:
    SUBMITTED = "submitted"
    CHECKED = "checked"
    PASSED = "passed"
    FAILED = "failed"

    ALL: FrozenSet[str] = frozenset({SUBMITTED, CHECKED, PASSED, FAILED})


class ProgressStatus:
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

    ALL: FrozenSet[str] = frozenset({NOT_STARTED, IN_PROGRESS, COMPLETED})


class ChapterStatus:
    DRAFT = "draft"
    GENERATING = "generating"
    REVIEWING = "reviewing"
    BLOCKED = "blocked"
    READY = "ready"
    PUBLISHED = "published"

    ALL: FrozenSet[str] = frozenset(
        {DRAFT, GENERATING, REVIEWING, BLOCKED, READY, PUBLISHED}
    )


class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    ALL: FrozenSet[str] = frozenset({QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED})


# Allowed transitions. Key: current state, value: allowed next states.
TRANSITIONS: Dict[str, Dict[str, FrozenSet[str]]] = {
    "course": {
        CourseStatus.DRAFT: frozenset({CourseStatus.INDEXING}),
        CourseStatus.INDEXING: frozenset(
            {CourseStatus.OUTLINE_READY, CourseStatus.FAILED}
        ),
        CourseStatus.OUTLINE_READY: frozenset(
            {CourseStatus.OUTLINE_APPROVED, CourseStatus.INDEXING, CourseStatus.FAILED}
        ),
        CourseStatus.OUTLINE_APPROVED: frozenset({CourseStatus.GENERATING}),
        CourseStatus.GENERATING: frozenset({CourseStatus.READY, CourseStatus.FAILED}),
        CourseStatus.READY: frozenset({CourseStatus.OUTLINE_READY, CourseStatus.GENERATING}),
        CourseStatus.FAILED: frozenset(
            {CourseStatus.INDEXING, CourseStatus.OUTLINE_READY, CourseStatus.GENERATING}
        ),
    },
    "version": {
        VersionStatus.DRAFT: frozenset({VersionStatus.GENERATING}),
        VersionStatus.GENERATING: frozenset(
            {VersionStatus.PUBLISHED, VersionStatus.FAILED}
        ),
        VersionStatus.PUBLISHED: frozenset(),
        VersionStatus.FAILED: frozenset(),
    },
    "chapter_review": {
        ChapterReviewStatus.PENDING: frozenset(
            {ChapterReviewStatus.PASSED, ChapterReviewStatus.ESCALATED}
        ),
        ChapterReviewStatus.ESCALATED: frozenset(
            {ChapterReviewStatus.PASSED, ChapterReviewStatus.FAILED}
        ),
        ChapterReviewStatus.PASSED: frozenset(),
        ChapterReviewStatus.FAILED: frozenset(),
    },
    "chapter_validation": {
        ChapterValidationStatus.PENDING: frozenset(
            {ChapterValidationStatus.PASSED, ChapterValidationStatus.FAILED}
        ),
        ChapterValidationStatus.PASSED: frozenset(),
        ChapterValidationStatus.FAILED: frozenset(),
    },
    "evidence": {
        EvidenceStatus.PENDING: frozenset({EvidenceStatus.PROCESSING}),
        EvidenceStatus.PROCESSING: frozenset(
            {EvidenceStatus.READY, EvidenceStatus.FAILED}
        ),
        EvidenceStatus.READY: frozenset(),
        EvidenceStatus.FAILED: frozenset({EvidenceStatus.PENDING}),
    },
    "attempt": {
        AttemptStatus.SUBMITTED: frozenset({AttemptStatus.CHECKED}),
        AttemptStatus.CHECKED: frozenset(
            {AttemptStatus.PASSED, AttemptStatus.FAILED}
        ),
        AttemptStatus.PASSED: frozenset(),
        AttemptStatus.FAILED: frozenset(),
    },
    "progress": {
        ProgressStatus.NOT_STARTED: frozenset({ProgressStatus.IN_PROGRESS}),
        ProgressStatus.IN_PROGRESS: frozenset(
            {ProgressStatus.COMPLETED, ProgressStatus.NOT_STARTED}
        ),
        ProgressStatus.COMPLETED: frozenset({ProgressStatus.IN_PROGRESS}),
    },
    "chapter": {
        ChapterStatus.DRAFT: frozenset({ChapterStatus.GENERATING}),
        ChapterStatus.GENERATING: frozenset({ChapterStatus.REVIEWING}),
        ChapterStatus.REVIEWING: frozenset({ChapterStatus.BLOCKED, ChapterStatus.READY}),
        ChapterStatus.BLOCKED: frozenset({ChapterStatus.GENERATING}),
        ChapterStatus.READY: frozenset({ChapterStatus.PUBLISHED, ChapterStatus.GENERATING}),
        ChapterStatus.PUBLISHED: frozenset(),
    },
    "run": {
        RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
        RunStatus.RUNNING: frozenset(
            {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
        ),
        RunStatus.SUCCEEDED: frozenset(),
        RunStatus.FAILED: frozenset(),
        RunStatus.CANCELLED: frozenset(),
    },
}

# Valid state sets, for input validation.
VALID_STATES: Dict[str, FrozenSet[str]] = {
    "course": CourseStatus.ALL,
    "version": VersionStatus.ALL,
    "chapter_review": ChapterReviewStatus.ALL,
    "chapter_validation": ChapterValidationStatus.ALL,
    "evidence": EvidenceStatus.ALL,
    "attempt": AttemptStatus.ALL,
    "progress": ProgressStatus.ALL,
    "chapter": ChapterStatus.ALL,
    "run": RunStatus.ALL,
}


def allowed_next(machine: str, current: str) -> FrozenSet[str]:
    """The states `current` may legally move to on `machine`."""
    return TRANSITIONS[machine].get(current, frozenset())


def transition(machine: str, current: str, target: str) -> str:
    """Validate `current -> target` against the machine's table.

    Raises InvalidInputError when the target is not a known state or the
    transition is not allowed. Returns `target` on success so callers can use
    it directly as the new field value.
    """
    if target not in VALID_STATES[machine]:
        raise InvalidInputError(
            f"Unknown '{machine}' status: {target!r}. "
            f"Valid states: {sorted(VALID_STATES[machine])}"
        )
    if target not in allowed_next(machine, current):
        raise InvalidInputError(
            f"Illegal '{machine}' transition: {current} -> {target}."
        )
    return target


def is_terminal(machine: str, state: str) -> bool:
    """True when the state has no outgoing transitions."""
    return not allowed_next(machine, state)


def validate_outline_approval_payload(outline: Any) -> None:
    """Shape-check the approved outline object (exact 确认大纲 gate input).

    The generation pipeline (M3) fills it; this guard only enforces the
    minimal structural contract so a broken payload can never be stored as an
    approved outline.
    """
    if not isinstance(outline, dict):
        raise InvalidInputError("Outline must be an object.")
    chapters = outline.get("chapters")
    if not isinstance(chapters, list) or len(chapters) == 0:
        raise InvalidInputError(
            "Outline must contain a non-empty 'chapters' list."
        )
    for i, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            raise InvalidInputError(f"Outline chapter #{i} must be an object.")
        title = chapter.get("title")
        if not isinstance(title, str) or not title.strip():
            raise InvalidInputError(
                f"Outline chapter #{i} must have a non-empty string 'title'."
            )
    # The dependency graph is optional; when present it must be an object.
    graph = outline.get("dependency_graph")
    if graph is not None and not isinstance(graph, dict):
        raise InvalidInputError(
            "'dependency_graph' must be an object when provided."
        )


def normalize_approval(text: str) -> str:
    """Normalize approval text per PDR-003 decision 5.

    Rules: Unicode NFC normalization, per-line trim + trailing-whitespace
    strip, drop blank lines, tolerate a missing trailing newline. Anything
    that survives normalization differs — different indentation levels, extra
    internal whitespace, punctuation — fails the gate by design (the gate is
    *exact*).
    """
    normalized = unicodedata.normalize("NFC", text)
    # Only surrounding whitespace and one trailing newline are tolerated.
    # Internal whitespace/newlines remain exact and therefore meaningful.
    normalized = normalized.strip(" \t")
    if normalized.endswith("\n"):
        normalized = normalized[:-1]
    return normalized.strip(" \t")


def approval_matches(expected: str, provided: str) -> bool:
    """The exact-match 确认大纲 gate: normalized texts must be identical."""
    return normalize_approval(expected) == normalize_approval(provided)
