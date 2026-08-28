from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.course_service import CourseService
from api.course_v2_service import CourseV2Service
from api.models import CourseCoverageResponse
from open_notebook.course.contracts import (
    ChapterArtifact,
    ChapterSection,
    CourseOutlineArtifact,
    SourceLocator,
)
from open_notebook.course.models import (
    BibliographicSource,
    Chapter,
    Course,
    CourseEvidenceAnchor,
    CourseVersion,
    Lab,
)
from open_notebook.course.source_quality_service import (
    BibliographyConflictError,
    CoverageReference,
    SourceQualityService,
)
from open_notebook.course.workflow_service import CourseWorkflowService

SOURCE_ONE_HASH = "1" * 64
SOURCE_TWO_HASH = "2" * 64


def anchor(
    key: str,
    *,
    source: str = "source:one",
    role: str = "PRIMARY",
    index: int = 1,
    block_key: str = "Definition 1",
    quote: str = "A bounded source quote",
    content_sha256: str = SOURCE_ONE_HASH,
) -> CourseEvidenceAnchor:
    return CourseEvidenceAnchor(
        id=f"course_evidence_anchor:{key}",
        course="course:one",
        source=source,
        anchor_id=f"anchor:{key}",
        locator=SourceLocator(
            source_id=source,
            kind="pdf_page" if source == "source:one" else "pptx_slide",
            index=index,
            block_key=block_key,
            quote=quote,
            content_sha256=content_sha256,
            bbox=(0.1, 0.2, 0.8, 0.9),
        ),
        quote_sha256="a" * 64,
        source_role=role,
        is_current=True,
    )


def bibliography() -> BibliographicSource:
    return BibliographicSource(
        id="course_bibliographic_source:one",
        course="course:one",
        source="source:one",
        source_role="PRIMARY",
        authors=["Ada Lovelace"],
        title="Grounded Mathematics",
        manually_reviewed=True,
        created=datetime(2026, 8, 29, tzinfo=timezone.utc),
        updated=datetime(2026, 8, 29, tzinfo=timezone.utc),
    )


def coverage_inputs() -> tuple[list[CourseEvidenceAnchor], list[CoverageReference]]:
    anchors = [
        anchor("primary", index=2, block_key="Definition 1"),
        anchor(
            "supplement",
            source="source:two",
            role="SUPPLEMENT",
            index=3,
            block_key="Exercise 7",
            content_sha256=SOURCE_TWO_HASH,
        ),
        anchor("answer", index=4, block_key="Answer 7"),
        anchor(
            "low",
            source="source:two",
            role="SUPPLEMENT",
            index=5,
            block_key="Visual block",
            content_sha256=SOURCE_TWO_HASH,
        ),
        anchor("unused", index=6, block_key="Narrative block"),
    ]
    references = [
        CoverageReference(
            kind="concept",
            key="vectors",
            chapter_key=None,
            anchor_ids=("anchor:primary",),
        ),
        CoverageReference(
            kind="chapter",
            key="chapter-a",
            chapter_key="chapter-a",
            anchor_ids=("anchor:primary", "anchor:answer"),
        ),
        CoverageReference(
            kind="example",
            key="example-a",
            chapter_key="chapter-a",
            anchor_ids=("anchor:answer",),
        ),
        CoverageReference(
            kind="exercise",
            key="exercise-a",
            chapter_key="chapter-a",
            anchor_ids=("anchor:supplement",),
        ),
        CoverageReference(
            kind="lab",
            key="lab-a",
            chapter_key="chapter-a",
            anchor_ids=("anchor:low",),
        ),
        CoverageReference(
            kind="chapter",
            key="chapter-b",
            chapter_key="chapter-b",
            anchor_ids=("anchor:primary",),
        ),
        # A duplicate reference must not produce duplicate usage rows.
        CoverageReference(
            kind="chapter",
            key="chapter-b",
            chapter_key="chapter-b",
            anchor_ids=("anchor:primary",),
        ),
    ]
    return anchors, references


def test_coverage_reducer_is_stable_deduplicated_and_classifies_flags() -> None:
    anchors, references = coverage_inputs()

    report = SourceQualityService.build_coverage_report(
        course_id="course:one",
        course_version_id="course_version:current",
        anchors=anchors,
        bibliography=[bibliography()],
        references=references,
        chapter_keys=("chapter-a", "chapter-b"),
        exercise_count=501,
    )

    assert report["schema_version"] == 1
    assert [item["source_id"] for item in report["source_hashes"]] == [
        "source:one",
        "source:two",
    ]
    rows = {row["anchor_id"]: row for row in report["rows"]}
    assert rows["anchor:supplement"]["category"] == "exercise"
    assert rows["anchor:supplement"]["confidence"] == "high"
    assert rows["anchor:supplement"]["flags"] == [
        "supplement_only",
        "missing_bibliography",
    ]
    assert rows["anchor:low"]["flags"] == [
        "low_confidence",
        "supplement_only",
        "missing_bibliography",
    ]
    assert rows["anchor:unused"]["flags"] == [
        "unused",
        "low_confidence",
    ]
    primary_usages = rows["anchor:primary"]["usages"]
    assert primary_usages.count(
        {"kind": "chapter", "key": "chapter-b", "chapter_key": "chapter-b"}
    ) == 1
    assert report["chapter_flags"] == [
        {"chapter_key": "chapter-b", "flags": ["no_answer_source"]}
    ]
    assert report["flags"] == ["generation_limit_exceeded"]
    assert len(report["report_hash"]) == 64


def test_coverage_export_is_identical_after_input_reordering_and_private_free() -> None:
    anchors, references = coverage_inputs()
    bibliographic_records = [bibliography()]
    forward = SourceQualityService.build_coverage_report(
        course_id="course:one",
        course_version_id="course_version:current",
        anchors=anchors,
        bibliography=bibliographic_records,
        references=references,
        chapter_keys=("chapter-b", "chapter-a"),
        exercise_count=3,
    )
    reverse = SourceQualityService.build_coverage_report(
        course_id="course:one",
        course_version_id="course_version:current",
        anchors=list(reversed(anchors)),
        bibliography=bibliographic_records,
        references=list(reversed(references)),
        chapter_keys=("chapter-b", "chapter-a"),
        exercise_count=3,
    )

    forward_json = SourceQualityService.canonical_coverage_json(forward)
    reverse_json = SourceQualityService.canonical_coverage_json(reverse)

    assert forward_json == reverse_json
    assert forward["report_hash"] == reverse["report_hash"]
    exported = json.loads(forward_json)
    assert exported["rows"][0]["anchor_id"].startswith("anchor:")
    assert exported["rows"][0]["locator"]["content_sha256"]
    assert "quote" not in forward_json
    assert "preview_path" not in forward_json
    assert "/Users/" not in forward_json


def test_coverage_rejects_inconsistent_current_source_hashes() -> None:
    anchors, references = coverage_inputs()
    anchors.append(
        anchor(
            "changed",
            source="source:one",
            content_sha256="f" * 64,
        )
    )
    with pytest.raises(BibliographyConflictError, match="Source hash"):
        SourceQualityService.build_coverage_report(
            course_id="course:one",
            course_version_id="course_version:current",
            anchors=anchors,
            bibliography=[bibliography()],
            references=references,
            chapter_keys=("chapter-a", "chapter-b"),
            exercise_count=3,
        )


@pytest.mark.asyncio
async def test_current_anchor_loader_requires_the_exact_owned_snapshot() -> None:
    current = anchor("primary")
    course_record = Course(
        id="course:one",
        title="Current course",
        notebook="notebook:one",
        source_ids=["source:one"],
        primary_source_ids=["source:one"],
    )
    query = AsyncMock(return_value=[current.model_dump(mode="json")])
    service = SourceQualityService(
        query=query,
        course_loader=AsyncMock(return_value=course_record),
    )

    loaded = await service.load_current_anchors(
        "course:one", ("anchor:primary",)
    )

    assert tuple(item.anchor_id for item in loaded) == ("anchor:primary",)
    query.return_value = []
    with pytest.raises(BibliographyConflictError, match="snapshot changed"):
        await service.load_current_anchors(
            "course:one", ("anchor:primary",)
        )


@pytest.mark.asyncio
async def test_coverage_facade_uses_only_current_version_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outline = CourseOutlineArtifact(
        title="Current course",
        chapters=[
            {
                "key": "chapter-a",
                "title": "Chapter A",
                "purpose": "Use current evidence.",
                "objective_keys": ["vectors"],
                "anchor_ids": ["anchor:primary"],
                "lab_keys": ["lab-current"],
            }
        ],
        concepts=[
            {
                "key": "vectors",
                "label": "Vectors",
                "anchor_ids": ["anchor:primary"],
            }
        ],
    )
    artifact = ChapterArtifact(
        chapter_key="chapter-a",
        purpose="Use current evidence.",
        objectives=["Apply vectors."],
        sections=[
            ChapterSection(
                key="definition",
                title="Definition",
                markdown="Current structured material.",
                anchor_ids=["anchor:primary"],
                provenance="adapted",
            )
        ],
        citations=["anchor:primary"],
        attributions={
            "purpose": {"provenance": "adapted", "anchor_ids": ["anchor:primary"]},
            "prerequisites": [],
            "objectives": [
                {"provenance": "adapted", "anchor_ids": ["anchor:primary"]}
            ],
            "definitions": [],
            "misconceptions": [],
            "pitfalls": [],
            "quick_reference": [],
        },
    )
    course_record = Course(
        id="course:one",
        title="Current course",
        notebook="notebook:one",
        source_ids=["source:one"],
        primary_source_ids=["source:one"],
        outline_version_id="course_version:current",
    )
    version = CourseVersion(
        id="course_version:current",
        course="course:one",
        version_no=2,
        status="generating",
        outline_artifact=outline.model_dump(mode="json"),
    )
    chapter = Chapter(
        id="chapter:current",
        course_version="course_version:current",
        chapter_no=1,
        title="Chapter A",
        chapter_key="chapter-a",
        artifact=artifact.model_dump(mode="json"),
    )
    stale_lab = Lab(
        id="lab:stale",
        course_version="course_version:current",
        chapter="chapter:stale",
        lab_type="function_plot",
        payload={"not": "a current Lab spec"},
    )
    current_anchors, _references = coverage_inputs()
    current_anchors = [
        item
        for item in current_anchors
        if item.source == "source:one"
    ]
    source_quality = SourceQualityService(
        course_loader=AsyncMock(return_value=course_record)
    )
    source_quality.load_current_anchors = AsyncMock(  # type: ignore[method-assign]
        return_value=tuple(current_anchors)
    )
    source_quality.list_bibliography = AsyncMock(  # type: ignore[method-assign]
        return_value=(bibliography(),)
    )

    monkeypatch.setattr(
        CourseService, "get_course", AsyncMock(return_value=course_record)
    )
    monkeypatch.setattr(
        CourseService,
        "list_owned_current_anchor_ids",
        AsyncMock(
            return_value=tuple(item.anchor_id for item in current_anchors)
        ),
    )
    monkeypatch.setattr(CourseVersion, "get", AsyncMock(return_value=version))
    monkeypatch.setattr(
        CourseVersion, "chapters", AsyncMock(return_value=[chapter])
    )
    monkeypatch.setattr(CourseVersion, "labs", AsyncMock(return_value=[stale_lab]))
    monkeypatch.setattr(
        CourseWorkflowService,
        "resolve_current_chapter",
        AsyncMock(return_value=chapter),
    )
    monkeypatch.setattr(
        "api.course_v2_service.repo_query",
        AsyncMock(
            return_value=[
                {
                    "course": "course:one",
                    "course_version": "course_version:old",
                    "chapter": "chapter:stale",
                    "chapter_key": "chapter-a",
                }
            ]
        ),
    )

    report = await CourseV2Service(
        source_quality_service=source_quality
    ).coverage("course:one")

    usage_kinds = {
        usage.kind for row in report.rows for usage in row.usages
    }
    assert usage_kinds == {"concept", "chapter"}
    assert "exercise" not in usage_kinds
    assert "lab" not in usage_kinds
    assert report.course_version_id == "course_version:current"


def test_coverage_routes_return_valid_report_and_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.course_v2_service import course_v2_service
    from api.main import app

    anchors, references = coverage_inputs()
    report = CourseCoverageResponse.model_validate(
        SourceQualityService.build_coverage_report(
            course_id="course:one",
            course_version_id="course_version:current",
            anchors=anchors,
            bibliography=[bibliography()],
            references=references,
            chapter_keys=("chapter-a", "chapter-b"),
            exercise_count=3,
        )
    )
    monkeypatch.setattr(
        course_v2_service,
        "coverage",
        AsyncMock(return_value=report),
        raising=False,
    )
    client = TestClient(app)

    response = client.get("/api/courses/course:one/coverage")
    exported = client.get("/api/courses/course:one/coverage/export")

    assert response.status_code == exported.status_code == 200
    assert response.json()["report_hash"] == report.report_hash
    assert exported.headers["content-type"].startswith("application/json")
    assert exported.headers["content-disposition"] == (
        'attachment; filename="course-coverage-course-one.json"'
    )
    assert exported.json() == response.json()
