from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from surrealdb import AsyncSurreal

from api.course_service import CourseConflictError
from api.course_v2_service import CourseV2Service, course_v2_service
from api.models import CourseLabApprovalRequest
from open_notebook.course.authoring_service import (
    AuthoringService,
    DraftScope,
)
from open_notebook.course.contracts import (
    ChapterArtifact,
    ChapterSection,
    FunctionPlotLabSpec,
    LabPedagogy,
    LabVariable,
)
from open_notebook.course.models import Lab, canonical_lab_proposal_hash
from open_notebook.course.publication_service import (
    LabPublicationError,
    PublicationService,
)
from open_notebook.course.v2_contracts import ReplaceLabOperation
from open_notebook.exceptions import NotFoundError

NOW = datetime(2026, 8, 29, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app)


def _migration(version: str) -> str:
    return Path(
        f"open_notebook/database/migrations/{version}.surrealql"
    ).read_text(encoding="utf-8")


def _pedagogy(label: str = "Slope") -> LabPedagogy:
    return LabPedagogy(
        learning_objectives=["Relate the parameter to the graph."],
        prerequisite_concepts=["Cartesian coordinates"],
        variables=[
            LabVariable(key="a", label=label, range=(-2, 2))
        ],
        prediction_prompt="Predict the direction of change.",
        steps=["Record a prediction.", "Move the control."],
        expected_observations=["The graph changes continuously."],
        student_submission="Submit a prediction and one observation.",
        rubric=["Prediction is testable.", "Observation uses graph evidence."],
        error_boundaries=["Only claim behavior within the displayed domain."],
        accessible_alternative="Compare values in the data table.",
    )


def _spec(*, title: str = "Linear plot") -> FunctionPlotLabSpec:
    return FunctionPlotLabSpec(
        key="limit-plot",
        title=title,
        expressions=["a*x"],
        domain={"x": (-2, 2)},
        controls=[
            {"key": "a", "label": "Slope", "min": -2, "max": 2, "value": 1}
        ],
        anchor_ids=[],
        provenance="pedagogical",
        pedagogy=_pedagogy(),
    )


def _artifact(spec: FunctionPlotLabSpec | None = None) -> ChapterArtifact:
    return ChapterArtifact(
        chapter_key="limits",
        purpose="Understand limits.",
        prerequisites=[],
        objectives=["Evaluate a limit."],
        sections=[
            ChapterSection(
                key="intro",
                title="Introduction",
                markdown="Inspect the graph.",
                anchor_ids=[],
                provenance="pedagogical",
            )
        ],
        labs=[spec or _spec()],
        citations=[],
        attributions={
            "purpose": {"provenance": "pedagogical", "anchor_ids": []},
            "prerequisites": [],
            "objectives": [
                {"provenance": "pedagogical", "anchor_ids": []}
            ],
            "definitions": [],
            "misconceptions": [],
            "pitfalls": [],
            "quick_reference": [],
        },
    )


def _scope(
    *,
    chapter_id: str = "chapter:one",
    chapter_status: str = "ready",
    version_status: str = "generating",
) -> DraftScope:
    return DraftScope(
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_id=chapter_id,
        chapter_key="limits",
        chapter_status=chapter_status,
        version_status=version_status,
        allowed_anchor_ids=(),
    )


def _request(proposal_hash: str, *, reason: str = "Checked every teaching field."):
    return CourseLabApprovalRequest(
        confirmation="确认实验方案",
        proposal_hash=proposal_hash,
        reason=reason,
    )


def test_canonical_proposal_hash_is_stable_and_covers_all_lab_content() -> None:
    payload = _spec().model_dump(mode="json", by_alias=True)
    reordered = {key: payload[key] for key in reversed(tuple(payload))}

    assert canonical_lab_proposal_hash(payload) == canonical_lab_proposal_hash(
        reordered
    )
    assert canonical_lab_proposal_hash(payload) == canonical_lab_proposal_hash(
        _spec().model_dump(mode="json", by_alias=True)
    )

    changed = deepcopy(payload)
    changed["pedagogy"]["prediction_prompt"] = "Predict a different outcome."
    assert canonical_lab_proposal_hash(changed) != canonical_lab_proposal_hash(
        payload
    )


def test_lab_approval_request_is_exact_strict_and_has_no_client_timestamp() -> None:
    with pytest.raises(ValidationError):
        CourseLabApprovalRequest.model_validate(
            {
                "confirmation": "确认实验",
                "proposal_hash": "a" * 64,
                "reason": "Checked.",
            }
        )
    with pytest.raises(ValidationError):
        CourseLabApprovalRequest.model_validate(
            {
                "confirmation": "确认实验方案",
                "proposal_hash": "a" * 64,
                "reason": "   ",
            }
        )
    with pytest.raises(ValidationError):
        CourseLabApprovalRequest.model_validate(
            {
                "confirmation": "确认实验方案",
                "proposal_hash": "a" * 64,
                "reason": "Checked.",
                "approved_at": "2000-01-01T00:00:00Z",
            }
        )


@pytest.mark.asyncio
async def test_publication_requires_current_complete_and_exactly_approved_labs() -> None:
    payload = _spec().model_dump(mode="json", by_alias=True)
    proposal_hash = canonical_lab_proposal_hash(payload)
    ready = Lab(
        id="lab:one",
        course_version="course_version:one",
        chapter="chapter:one",
        lab_type="function_plot",
        payload=payload,
        proposal_hash=proposal_hash,
        approved_hash=proposal_hash,
        approved_at=NOW,
        approval_reason="Checked every teaching field.",
    )
    query = AsyncMock(return_value=[ready.model_dump(mode="json")])

    await PublicationService(revision_query=query).assert_labs_ready(_scope())

    call = query.await_args
    assert call is not None
    params = call.args[1]
    assert str(params["version"]) == "course_version:one"
    assert str(params["chapter"]) == "chapter:one"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("missing", "required"),
        ("legacy", "pedagogy"),
        ("no_proposal", "proposal hash"),
        ("tampered_proposal", "proposal hash"),
        ("unapproved", "approval"),
        ("stale_approval", "approval"),
        ("foreign_chapter", "stale chapter"),
    ],
)
async def test_publication_fails_closed_for_unapproved_or_stale_lab(
    change: str, message: str
) -> None:
    payload = _spec().model_dump(mode="json", by_alias=True)
    proposal_hash = canonical_lab_proposal_hash(payload)
    lab = Lab(
        id="lab:one",
        course_version="course_version:one",
        chapter="chapter:one",
        lab_type="function_plot",
        payload=payload,
        proposal_hash=proposal_hash,
        approved_hash=proposal_hash,
        approved_at=NOW,
        approval_reason="Checked.",
    )
    records: list[dict[str, Any]] = [lab.model_dump(mode="json")]
    if change == "missing":
        records = []
    elif change == "legacy":
        legacy = _spec().model_dump(mode="json", by_alias=True)
        legacy.pop("pedagogy")
        records[0]["payload"] = legacy
        records[0]["proposal_hash"] = canonical_lab_proposal_hash(legacy)
        records[0]["approved_hash"] = records[0]["proposal_hash"]
    elif change == "no_proposal":
        records[0]["proposal_hash"] = None
        records[0]["approved_hash"] = None
    elif change == "tampered_proposal":
        records[0]["proposal_hash"] = "b" * 64
        records[0]["approved_hash"] = "b" * 64
    elif change == "unapproved":
        records[0]["approved_hash"] = None
        records[0]["approved_at"] = None
        records[0]["approval_reason"] = None
    elif change == "stale_approval":
        records[0]["approved_hash"] = "b" * 64
    else:
        records[0]["chapter"] = "chapter:old"

    with pytest.raises(LabPublicationError, match=message):
        await PublicationService(
            revision_query=AsyncMock(return_value=records)
        ).assert_labs_ready(_scope())


@pytest.mark.asyncio
async def test_approval_is_atomic_idempotent_and_edit_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("course_lab_approval", "course_lab_approval")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    await database.query(
        "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
    )
    for version in ("24", "25", "26", "27", "28", "29"):
        await database.query(_migration(version))

    artifact = _artifact().model_dump(mode="json")
    payload = cast(dict[str, Any], artifact["labs"][0])
    proposal_hash = canonical_lab_proposal_hash(payload)
    await database.query(
        """
        CREATE notebook:one SET name = 'Notebook';
        CREATE course:one SET title = 'Calculus', notebook = notebook:one,
            source_ids = [], primary_source_ids = [], supplement_source_ids = [],
            outline_version_id = course_version:one;
        CREATE course_version:one SET course = course:one, version_no = 1,
            status = 'generating';
        CREATE chapter:one SET course_version = course_version:one,
            chapter_no = 1, chapter_key = 'limits', version_no = 1,
            title = 'Limits', status = 'ready', review_status = 'passed',
            validation_status = 'passed', artifact = $artifact;
        CREATE lab:one SET course_version = course_version:one,
            chapter = chapter:one, lab_type = 'function_plot',
            payload = $payload, proposal_hash = $proposal_hash;
        """,
        {
            "artifact": artifact,
            "payload": payload,
            "proposal_hash": proposal_hash,
        },
    )

    service = CourseV2Service(clock=lambda: NOW)
    monkeypatch.setattr(service, "_draft_scope", AsyncMock(return_value=_scope()))
    approved = await service.approve_lab_proposal(
        "course:one", "limits", "limit-plot", _request(proposal_hash)
    )
    replayed = await service.approve_lab_proposal(
        "course:one", "limits", "limit-plot", _request(proposal_hash)
    )
    assert approved.approved_hash == proposal_hash
    assert approved.approved_at == NOW
    assert replayed.approved_at == NOW

    with pytest.raises(CourseConflictError, match="hash"):
        await service.approve_lab_proposal(
            "course:one", "limits", "limit-plot", _request("b" * 64)
        )

    await database.query("UPDATE chapter:one SET status = 'reviewing';")
    authoring = AuthoringService(clock=lambda: NOW)
    editable_scope = _scope(chapter_status="reviewing")
    draft = await authoring.get_draft(editable_scope)
    replacement = _spec(title="Edited linear plot")
    await authoring.save_operation(
        draft,
        ReplaceLabOperation(
            kind="replace_lab",
            block_key="limit-plot",
            lab_spec=replacement,
        ),
        expected_revision=draft.revision_token,
    )
    edited = Lab(
        **cast(
            dict[str, Any],
            await database.query("SELECT * FROM ONLY lab:one;"),
        )
    )
    assert edited.payload["title"] == "Edited linear plot"
    assert edited.proposal_hash == canonical_lab_proposal_hash(
        replacement.model_dump(mode="json", by_alias=True)
    )
    assert edited.approved_hash is None
    assert edited.approved_at is None
    assert edited.approval_reason is None
    await database.close()


@pytest.mark.asyncio
async def test_approval_rejects_foreign_or_immutable_lab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal_hash = canonical_lab_proposal_hash(
        _spec().model_dump(mode="json", by_alias=True)
    )
    service = CourseV2Service(lab_query=AsyncMock(return_value=[]))
    monkeypatch.setattr(service, "_draft_scope", AsyncMock(return_value=_scope()))
    with pytest.raises(NotFoundError):
        await service.approve_lab_proposal(
            "course:one", "limits", "limit-plot", _request(proposal_hash)
        )

    monkeypatch.setattr(
        service,
        "_draft_scope",
        AsyncMock(return_value=_scope(chapter_status="published")),
    )
    with pytest.raises(CourseConflictError, match="immutable"):
        await service.approve_lab_proposal(
            "course:one", "limits", "limit-plot", _request(proposal_hash)
        )


def test_lab_approval_route_maps_validation_and_conflict(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approve = AsyncMock(side_effect=CourseConflictError("proposal hash is stale"))
    monkeypatch.setattr(course_v2_service, "approve_lab_proposal", approve)
    url = "/api/courses/course:one/chapters/limits/labs/limit-plot/approve"

    wrong_phrase = client.post(
        url,
        json={
            "confirmation": "确认实验",
            "proposal_hash": "a" * 64,
            "reason": "Checked.",
        },
    )
    injected = client.post(
        url,
        json={
            "confirmation": "确认实验方案",
            "proposal_hash": "a" * 64,
            "reason": "Checked.",
            "approved_at": "2000-01-01T00:00:00Z",
        },
    )
    stale = client.post(
        url,
        json={
            "confirmation": "确认实验方案",
            "proposal_hash": "a" * 64,
            "reason": "Checked.",
        },
    )

    assert wrong_phrase.status_code == 422
    assert injected.status_code == 422
    assert stale.status_code == 409
    approve.assert_awaited_once()
