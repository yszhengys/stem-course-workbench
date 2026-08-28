"""Auditable human verification operations for chapter academic artifacts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from surrealdb import AsyncSurreal

from api.course_service import CourseConflictError
from api.course_v2_service import CourseV2Service, course_v2_service
from api.models import CourseAcademicVerificationRequest
from open_notebook.course.authoring_service import (
    AuthoringService,
    DraftConflictError,
    DraftImmutableError,
    DraftScope,
    DraftState,
)
from open_notebook.course.contracts import (
    AcademicVerification,
    ChapterArtifact,
    ExerciseArtifact,
    FormulaArtifact,
    WorkedExampleArtifact,
)
from open_notebook.course.v2_contracts import (
    ReplaceFormulaOperation,
    ReplaceTextOperation,
    VerifyAcademicArtifactOperation,
)
from open_notebook.exceptions import InvalidInputError

NOW = datetime(2026, 8, 29, 8, 30, tzinfo=timezone.utc)


@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app)


def _artifact() -> ChapterArtifact:
    return ChapterArtifact(
        chapter_key="limits",
        purpose="Understand limits.",
        prerequisites=[],
        objectives=["Evaluate limits."],
        sections=[
            {
                "key": "definition",
                "title": "Definition",
                "markdown": "A limit is an approached value.",
                "anchor_ids": ["anchor:one"],
                "provenance": "adapted",
            }
        ],
        definitions=[],
        formulas=[
            FormulaArtifact(
                key="limit-law",
                latex="x+0",
                meaning="Additive identity.",
                anchor_ids=["anchor:one"],
                provenance="adapted",
            )
        ],
        worked_examples=[
            WorkedExampleArtifact(
                key="worked-one",
                prompt="Compute 2 + 2.",
                steps=["Add the terms."],
                answer="4",
                anchor_ids=["anchor:one"],
                provenance="adapted",
            )
        ],
        exercises=[
            ExerciseArtifact(
                key="legacy-one",
                prompt="Compute 3 + 3.",
                difficulty="core",
                hints=["Add."],
                answer="6",
                transfer_task="Compute 4 + 4.",
                anchor_ids=["anchor:one"],
                provenance="adapted",
            )
        ],
        citations=["anchor:one"],
        attributions={
            "purpose": {"provenance": "adapted", "anchor_ids": ["anchor:one"]},
            "prerequisites": [],
            "objectives": [
                {"provenance": "adapted", "anchor_ids": ["anchor:one"]}
            ],
            "definitions": [],
            "misconceptions": [],
            "pitfalls": [],
            "quick_reference": [],
        },
    )


def _scope(
    *,
    chapter_status: str = "reviewing",
    version_status: str = "generating",
) -> DraftScope:
    return DraftScope(
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_id="chapter:one",
        chapter_key="limits",
        chapter_status=chapter_status,
        version_status=version_status,
        allowed_anchor_ids=("anchor:one", "anchor:two"),
    )


def _draft(*, scope: DraftScope | None = None, revision_no: int = 3) -> DraftState:
    return DraftState(
        scope=scope or _scope(),
        artifact=_artifact(),
        revision_no=revision_no,
        revision_id=f"course_draft_revision:r{revision_no}",
        revision_status="validated",
    )


def _verification_operation(
    draft: DraftState,
    *,
    target_kind: str = "formula",
    target_key: str = "limit-law",
    exact_value: str = "x+0",
) -> VerifyAcademicArtifactOperation:
    return VerifyAcademicArtifactOperation(
        kind="verify_academic_artifact",
        target_kind=target_kind,
        target_key=target_key,
        exact_value=exact_value,
        verification=AcademicVerification(
            level="L3",
            method="human_review",
            anchor_ids=["anchor:one"],
            reason="Checked against the cited source.",
            verified_at=NOW,
            artifact_hash=draft.artifact_hash,
        ),
    )


@pytest.mark.parametrize(
    "target_kind, target_key, exact_value, field",
    [
        ("formula", "limit-law", "x+0", "formulas"),
        ("worked_example", "worked-one", "4", "worked_examples"),
        ("legacy_exercise", "legacy-one", "6", "exercises"),
    ],
)
def test_human_verification_is_bound_to_the_exact_pre_operation_snapshot(
    target_kind: str,
    target_key: str,
    exact_value: str,
    field: str,
) -> None:
    service = AuthoringService(clock=lambda: NOW)
    draft = _draft()

    change = service.apply_operation(
        draft,
        _verification_operation(
            draft,
            target_kind=target_kind,
            target_key=target_key,
            exact_value=exact_value,
        ),
        expected_revision=draft.revision_token,
    )

    target = getattr(change.draft.artifact, field)[0]
    assert target.verification.level == "L3"
    assert target.verification.method == "human_review"
    assert target.verification.verified_at == NOW
    assert target.verification.artifact_hash == draft.artifact_hash
    assert change.revision.invalidated_checks == ()
    assert change.revision.base_artifact_hash == draft.artifact_hash


def test_human_verification_rejects_value_hash_anchor_and_immutability_conflicts() -> None:
    service = AuthoringService(clock=lambda: NOW)
    draft = _draft()
    operation = _verification_operation(draft)

    with pytest.raises(DraftConflictError, match="exact displayed value"):
        service.apply_operation(
            draft,
            operation.model_copy(update={"exact_value": "different"}),
            expected_revision=draft.revision_token,
        )
    stale_verification = operation.verification.model_copy(
        update={"artifact_hash": "f" * 64}
    )
    with pytest.raises(DraftConflictError, match="artifact hash"):
        service.apply_operation(
            draft,
            operation.model_copy(update={"verification": stale_verification}),
            expected_revision=draft.revision_token,
        )
    foreign_verification = operation.verification.model_copy(
        update={"anchor_ids": ["anchor:foreign"]}
    )
    with pytest.raises(DraftConflictError, match="evidence"):
        service.apply_operation(
            draft,
            operation.model_copy(update={"verification": foreign_verification}),
            expected_revision=draft.revision_token,
        )
    published = _draft(scope=_scope(chapter_status="published", version_status="published"))
    with pytest.raises(DraftImmutableError):
        service.apply_operation(
            published,
            _verification_operation(published),
            expected_revision=published.revision_token,
        )


def test_answer_bearing_edits_reset_prior_human_verification_to_l0() -> None:
    service = AuthoringService(clock=lambda: NOW)
    original = _draft()

    formula_verified = service.apply_operation(
        original,
        _verification_operation(original),
        expected_revision=original.revision_token,
    ).draft
    formula_change = service.apply_operation(
        formula_verified,
        ReplaceFormulaOperation(
            kind="replace_formula",
            block_key="limit-law",
            latex="x+1",
            anchor_ids=["anchor:one"],
        ),
        expected_revision=formula_verified.revision_token,
    )
    assert formula_change.draft.artifact.formulas[0].verification == AcademicVerification(
        level="L0", method="structure"
    )

    for target_kind, key, block_key, value, collection in (
        ("worked_example", "worked-one", "worked-example-worked-one-answer", "5", "worked_examples"),
        ("legacy_exercise", "legacy-one", "legacy-exercise-legacy-one-answer", "7", "exercises"),
    ):
        base = _draft()
        verified = service.apply_operation(
            base,
            _verification_operation(
                base,
                target_kind=target_kind,
                target_key=key,
                exact_value="4" if target_kind == "worked_example" else "6",
            ),
            expected_revision=base.revision_token,
        ).draft
        changed = service.apply_operation(
            verified,
            ReplaceTextOperation(
                kind="replace_text",
                block_key=block_key,
                text=value,
                anchor_ids=["anchor:one"],
            ),
            expected_revision=verified.revision_token,
        )
        verification = getattr(changed.draft.artifact, collection)[0].verification
        assert verification == AcademicVerification(level="L0", method="structure")


async def _service_with_memory_authoring(
    monkeypatch: pytest.MonkeyPatch,
    draft: DraftState,
) -> tuple[CourseV2Service, AsyncMock]:
    engine = AuthoringService(clock=lambda: NOW)
    authoring = cast(AuthoringService, AsyncMock(spec=AuthoringService))
    get_draft = AsyncMock(return_value=draft)

    async def save_operation(
        current: DraftState,
        operation: VerifyAcademicArtifactOperation,
        *,
        expected_revision: str,
    ) -> DraftState:
        return engine.apply_operation(
            current, operation, expected_revision=expected_revision
        ).draft

    save = AsyncMock(side_effect=save_operation)
    authoring.get_draft = get_draft  # type: ignore[method-assign]
    authoring.save_operation = save  # type: ignore[method-assign]
    service = CourseV2Service(authoring_service=authoring, clock=lambda: NOW)
    monkeypatch.setattr(service, "_draft_scope", AsyncMock(return_value=draft.scope))
    return service, save


@pytest.mark.asyncio
async def test_facade_constructs_server_owned_l3_and_replays_immediate_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _draft()
    service, save = await _service_with_memory_authoring(monkeypatch, draft)
    request = CourseAcademicVerificationRequest(
        revision_token=draft.revision_token,
        exact_value_confirmation="x+0",
        reason="Checked against the cited source.",
        anchor_ids=["anchor:one"],
    )

    first = await service.verify_academic_artifact(
        "course:one", "limits", "formula", "limit-law", request
    )
    operation = cast(VerifyAcademicArtifactOperation, save.await_args.args[1])
    assert operation.verification == AcademicVerification(
        level="L3",
        method="human_review",
        anchor_ids=["anchor:one"],
        reason="Checked against the cited source.",
        verified_at=NOW,
        artifact_hash=draft.artifact_hash,
    )

    authoring = cast(AuthoringService, service.authoring_service)
    replay_state = AuthoringService(clock=lambda: NOW).apply_operation(
        draft,
        operation,
        expected_revision=draft.revision_token,
    ).draft
    cast(AsyncMock, authoring.get_draft).return_value = replay_state
    replay = await service.verify_academic_artifact(
        "course:one", "limits", "formula", "limit-law", request
    )

    assert replay.revision_token == first.revision_token
    assert save.await_count == 1


@pytest.mark.asyncio
async def test_facade_rejects_exact_value_stale_revision_and_foreign_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _draft()
    service, _save = await _service_with_memory_authoring(monkeypatch, draft)

    with pytest.raises(InvalidInputError, match="(?i)exact displayed value"):
        await service.verify_academic_artifact(
            "course:one",
            "limits",
            "formula",
            "limit-law",
            CourseAcademicVerificationRequest(
                revision_token=draft.revision_token,
                exact_value_confirmation="wrong",
                reason="Checked.",
                anchor_ids=["anchor:one"],
            ),
        )
    with pytest.raises(InvalidInputError, match="invalid"):
        await service.verify_academic_artifact(
            "course:one",
            "limits",
            "formula",
            "limit-law",
            CourseAcademicVerificationRequest(
                revision_token=draft.revision_token,
                exact_value_confirmation="x+0",
                reason="<script>not audit text</script>",
                anchor_ids=["anchor:one"],
            ),
        )
    for token, anchors in (("0" * 64, ["anchor:one"]), (draft.revision_token, ["anchor:foreign"])):
        with pytest.raises(CourseConflictError):
            await service.verify_academic_artifact(
                "course:one",
                "limits",
                "formula",
                "limit-law",
                CourseAcademicVerificationRequest(
                    revision_token=token,
                    exact_value_confirmation="x+0",
                    reason="Checked.",
                    anchor_ids=anchors,
                ),
            )


def _draft_response() -> dict[str, object]:
    draft = _draft()
    return {
        "chapter_key": draft.scope.chapter_key,
        "chapter_status": draft.scope.chapter_status,
        "editable": draft.editable,
        "revision_no": draft.revision_no,
        "revision_token": draft.revision_token,
        "revision_status": draft.revision_status,
        "artifact_hash": draft.artifact_hash,
        "artifact": draft.artifact.model_dump(mode="json"),
        "exercises": [],
    }


def _migration_sql(version: str) -> str:
    return Path(f"open_notebook/database/migrations/{version}.surrealql").read_text()


@pytest.mark.asyncio
async def test_human_verification_is_persisted_as_an_immutable_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("academic_verification", "academic_verification")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    await database.query(
        "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
    )
    for version in ("24", "25", "26"):
        await database.query(_migration_sql(version))
    await database.query(
        """
        CREATE notebook:one SET name = 'Notebook';
        CREATE course:one SET title = 'Calculus', notebook = notebook:one,
            source_ids = [], primary_source_ids = [], supplement_source_ids = [];
        CREATE course_version:one SET course = course:one, version_no = 1,
            status = 'generating';
        CREATE chapter:one SET course_version = course_version:one,
            chapter_no = 1, chapter_key = 'limits', title = 'Limits',
            status = 'reviewing', review_status = 'pending',
            validation_status = 'pending', artifact = $artifact,
            input_hash = 'generated';
        """,
        {"artifact": _artifact().model_dump(mode="json")},
    )
    service = AuthoringService(clock=lambda: NOW)
    loaded = await service.get_draft(_scope())

    saved = await service.save_operation(
        loaded,
        _verification_operation(loaded),
        expected_revision=loaded.revision_token,
    )

    rows = cast(
        list[dict[str, Any]],
        await database.query(
            "SELECT operation, base_artifact_hash, artifact_hash "
            "FROM course_draft_revision;"
        ),
    )
    persisted = cast(
        dict[str, Any],
        await database.query("SELECT artifact FROM ONLY chapter:one;"),
    )
    assert len(rows) == 1
    assert rows[0]["operation"]["kind"] == "verify_academic_artifact"
    assert rows[0]["operation"]["verification"]["artifact_hash"] == (
        loaded.artifact_hash
    )
    assert rows[0]["base_artifact_hash"] == loaded.artifact_hash
    assert rows[0]["artifact_hash"] == saved.artifact_hash
    assert persisted["artifact"]["formulas"][0]["verification"]["level"] == "L3"
    await database.close()


def test_academic_verification_route_is_strict_and_maps_validation_errors(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = AsyncMock(return_value=_draft_response())
    monkeypatch.setattr(course_v2_service, "verify_academic_artifact", verify)
    url = "/api/courses/course:one/chapters/limits/artifacts/formula/limit-law/verify"
    payload = {
        "revision_token": "a" * 64,
        "exact_value_confirmation": "x+0",
        "reason": "Checked against the cited source.",
        "anchor_ids": ["anchor:one"],
    }

    accepted = client.post(url, json=payload)
    injected = client.post(url, json={**payload, "level": "L3"})
    generic_patch = client.patch(
        "/api/courses/course:one/chapters/limits/draft",
        json={
            "revision_token": "a" * 64,
            "operation": {
                "kind": "verify_academic_artifact",
                "target_kind": "formula",
                "target_key": "limit-law",
                "exact_value": "x+0",
                "verification": {
                    "level": "L3",
                    "method": "human_review",
                    "anchor_ids": ["anchor:one"],
                    "reason": "Client-controlled verification.",
                    "verified_at": "2026-08-29T08:30:00Z",
                    "artifact_hash": "b" * 64,
                },
            },
        },
    )
    verify.side_effect = InvalidInputError("Exact displayed value does not match.")
    mismatch = client.post(url, json=payload)
    verify.side_effect = CourseConflictError("Draft revision token is stale.")
    stale = client.post(url, json=payload)

    assert accepted.status_code == 200
    assert injected.status_code == 422
    assert generic_patch.status_code == 422
    assert mismatch.status_code == 422
    assert stale.status_code == 409
