import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.course_service import CourseConflictError, CourseService
from open_notebook.course.evidence_service import EvidenceInputError
from open_notebook.course.models import Chapter, Course, CourseVersion


def _artifact_hash(artifact: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _approved_outline() -> dict:
    return {
        "title": "Calculus",
        "chapters": [
            {
                "key": "limits",
                "title": "Limits",
                "purpose": "Learn limits.",
                "objective_keys": ["limit"],
                "anchor_ids": ["anchor:outline", "anchor:shared"],
                "lab_keys": ["limit-plot"],
            }
        ],
        "concepts": [
            {
                "key": "limit",
                "label": "Limit",
                "anchor_ids": ["anchor:concept", "anchor:shared"],
            }
        ],
        "dependency_edges": [],
    }


def _chapter_artifact() -> dict:
    return {
        "chapter_key": "limits",
        "purpose": "Learn limits.",
        "prerequisites": ["Functions"],
        "objectives": ["Understand limits"],
        "sections": [
            {
                "key": "section",
                "title": "Limits",
                "markdown": "Grounded section.",
                "anchor_ids": ["anchor:section", "anchor:shared"],
                "provenance": "adapted",
            }
        ],
        "definitions": ["A limit describes nearby behavior."],
        "formulas": [
            {
                "key": "formula",
                "latex": "x",
                "meaning": "Identity.",
                "oracle_expression": "x",
                "anchor_ids": ["anchor:formula"],
                "provenance": "adapted",
            }
        ],
        "worked_examples": [
            {
                "key": "example",
                "prompt": "Evaluate 1 + 1.",
                "steps": ["Add."],
                "answer": "2",
                "oracle_expression": "1 + 1",
                "oracle_answer": 2,
                "anchor_ids": ["anchor:example"],
                "provenance": "adapted",
            }
        ],
        "labs": [
            {
                "kind": "function_plot",
                "key": "limit-plot",
                "title": "Plot",
                "expressions": ["x"],
                "anchor_ids": [],
                "provenance": "pedagogical",
            }
        ],
        "misconceptions": ["A limit need not equal the function value."],
        "pitfalls": ["Check both sides."],
        "exercises": [
            {
                "key": "exercise",
                "prompt": "Evaluate a limit.",
                "difficulty": "core",
                "answer": "1",
                "transfer_task": "Evaluate another limit.",
                "anchor_ids": ["anchor:exercise"],
                "provenance": "adapted",
            }
        ],
        "quick_reference": ["Check both sides."],
        "citations": ["anchor:citation", "anchor:shared"],
        "attributions": {
            "purpose": {
                "anchor_ids": ["anchor:purpose"],
                "provenance": "adapted",
            },
            "prerequisites": [
                {"anchor_ids": [], "provenance": "pedagogical"}
            ],
            "objectives": [
                {"anchor_ids": ["anchor:objective"], "provenance": "adapted"}
            ],
            "definitions": [
                {"anchor_ids": ["anchor:definition"], "provenance": "adapted"}
            ],
            "misconceptions": [
                {"anchor_ids": ["anchor:misconception"], "provenance": "adapted"}
            ],
            "pitfalls": [
                {"anchor_ids": ["anchor:pitfall"], "provenance": "adapted"}
            ],
            "quick_reference": [
                {"anchor_ids": ["anchor:quick"], "provenance": "adapted"}
            ],
        },
        "physics_checks": [],
    }


def _records(*, chapter_status: str = "ready") -> tuple[Course, CourseVersion, Chapter]:
    outline = _approved_outline()
    course = Course(
        id="course:one",
        title="Calculus",
        notebook="notebook:one",
        status="generating",
        outline_version_id="course_version:one",
    )
    version = CourseVersion(
        id="course_version:one",
        course="course:one",
        version_no=1,
        status="generating",
        outline_artifact=outline,
        outline_hash=_artifact_hash(outline),
        approved_at="2026-08-18T00:00:00Z",
        confirmation="确认大纲",
    )
    chapter = Chapter(
        id="chapter:one",
        course_version="course_version:one",
        chapter_no=1,
        chapter_key="limits",
        title="Limits",
        status=chapter_status,
        review_status="passed",
        validation_status="passed",
        artifact=_chapter_artifact(),
        input_hash="generated-content-hash",
    )
    return course, version, chapter


def _patch_records(monkeypatch, course, version, chapter) -> None:
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(Chapter, "get", AsyncMock(return_value=chapter))


@pytest.mark.asyncio
async def test_publish_chapter_revalidates_stable_unique_evidence_before_save(
    monkeypatch,
):
    course, version, chapter = _records()
    _patch_records(monkeypatch, course, version, chapter)
    finding = {
        "kind": "review",
        "severity": "info",
        "item_key": "reviewed",
        "anchor_ids": ["anchor:finding", "anchor:shared"],
        "status": "resolved",
        "message": "Reviewed.",
    }
    monkeypatch.setattr(
        "api.course_service.repo_query",
        AsyncMock(return_value=[{"finding": finding}]),
    )
    grounded = AsyncMock(return_value=([], {}, []))
    monkeypatch.setattr(
        "api.course_service.CourseWorkflowService.grounded_inputs", grounded
    )
    save = AsyncMock()
    monkeypatch.setattr(Chapter, "save", save)

    await CourseService.publish_chapter(
        "course:one", "course_version:one", "chapter:one"
    )

    assert grounded.await_args is not None
    assert grounded.await_args.kwargs["anchor_ids"] == [
        "anchor:outline",
        "anchor:shared",
        "anchor:citation",
        "anchor:purpose",
        "anchor:objective",
        "anchor:definition",
        "anchor:misconception",
        "anchor:pitfall",
        "anchor:quick",
        "anchor:section",
        "anchor:formula",
        "anchor:example",
        "anchor:exercise",
        "anchor:finding",
    ]
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_chapter_source_change_blocks_without_state_mutation(monkeypatch):
    course, version, chapter = _records()
    _patch_records(monkeypatch, course, version, chapter)
    monkeypatch.setattr("api.course_service.repo_query", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "api.course_service.CourseWorkflowService.grounded_inputs",
        AsyncMock(side_effect=EvidenceInputError("Source hash changed")),
    )
    save = AsyncMock()
    monkeypatch.setattr(Chapter, "save", save)
    original = chapter.model_dump()

    with pytest.raises(CourseConflictError, match="rebuild evidence"):
        await CourseService.publish_chapter(
            "course:one", "course_version:one", "chapter:one"
        )

    assert chapter.model_dump() == original
    save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("artifact", "input_hash"),
    [(None, "generated-content-hash"), ({"chapter_key": "limits"}, "hash"), (_chapter_artifact(), None)],
)
async def test_publish_chapter_fails_closed_for_manual_or_invalid_artifact(
    monkeypatch, artifact, input_hash
):
    course, version, chapter = _records()
    chapter.artifact = artifact
    chapter.input_hash = input_hash
    _patch_records(monkeypatch, course, version, chapter)
    query = AsyncMock(return_value=[])
    monkeypatch.setattr("api.course_service.repo_query", query)
    grounded = AsyncMock(return_value=([], {}, []))
    monkeypatch.setattr(
        "api.course_service.CourseWorkflowService.grounded_inputs", grounded
    )
    save = AsyncMock()
    monkeypatch.setattr(Chapter, "save", save)

    with pytest.raises(CourseConflictError, match="artifact"):
        await CourseService.publish_chapter(
            "course:one", "course_version:one", "chapter:one"
        )

    save.assert_not_awaited()
    grounded.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_version_revalidates_all_anchors_and_blocks_changed_source(
    monkeypatch,
):
    course, version, chapter = _records(chapter_status="published")
    _patch_records(monkeypatch, course, version, chapter)
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(
        "api.course_service.CourseWorkflowService.chapter_promotion_snapshot",
        AsyncMock(
            return_value=SimpleNamespace(
                current=chapter,
                succeeded_run_ids=("course_generation_run:one",),
                manual_chapter_ids=(),
            )
        ),
    )
    finding = {
        "kind": "review",
        "severity": "info",
        "item_key": "reviewed",
        "anchor_ids": ["anchor:finding", "anchor:shared"],
        "status": "resolved",
        "message": "Reviewed.",
    }

    async def query(statement: str, variables=None):
        del variables
        if "course_validation_finding" in statement:
            return [{"finding": finding}]
        raise AssertionError("publication transaction must not start")

    monkeypatch.setattr("api.course_service.repo_query", query)
    grounded = AsyncMock(side_effect=EvidenceInputError("Source hash changed"))
    monkeypatch.setattr(
        "api.course_service.CourseWorkflowService.grounded_inputs", grounded
    )
    original_version = version.model_dump()
    original_course = course.model_dump()

    with pytest.raises(CourseConflictError, match="rebuild evidence"):
        await CourseService.publish_version("course_version:one")

    assert grounded.await_args is not None
    assert grounded.await_args.kwargs["anchor_ids"] == [
        "anchor:outline",
        "anchor:shared",
        "anchor:concept",
        "anchor:citation",
        "anchor:purpose",
        "anchor:objective",
        "anchor:definition",
        "anchor:misconception",
        "anchor:pitfall",
        "anchor:quick",
        "anchor:section",
        "anchor:formula",
        "anchor:example",
        "anchor:exercise",
        "anchor:finding",
    ]
    assert version.model_dump() == original_version
    assert course.model_dump() == original_course


@pytest.mark.asyncio
async def test_publish_version_fails_closed_for_manual_current_chapter(monkeypatch):
    course, version, chapter = _records(chapter_status="published")
    chapter.input_hash = None
    _patch_records(monkeypatch, course, version, chapter)
    monkeypatch.setattr(CourseVersion, "chapters", AsyncMock(return_value=[chapter]))
    monkeypatch.setattr(
        "api.course_service.CourseWorkflowService.chapter_promotion_snapshot",
        AsyncMock(
            return_value=SimpleNamespace(
                current=chapter,
                succeeded_run_ids=(),
                manual_chapter_ids=("chapter:one",),
            )
        ),
    )
    transaction = AsyncMock(return_value=[])
    monkeypatch.setattr("api.course_service.repo_query", transaction)
    grounded = AsyncMock(return_value=([], {}, []))
    monkeypatch.setattr(
        "api.course_service.CourseWorkflowService.grounded_inputs", grounded
    )

    with pytest.raises(CourseConflictError, match="artifact"):
        await CourseService.publish_version("course_version:one")

    transaction.assert_not_awaited()
    grounded.assert_not_awaited()
