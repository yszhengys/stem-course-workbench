"""Concurrency, provenance, and validation gates for structured Course drafts."""

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
from api.course_v2_service import course_v2_service
from open_notebook.course.authoring_service import (
    AuthoringService,
    DraftConflictError,
    DraftImmutableError,
    DraftScope,
    DraftState,
)
from open_notebook.course.contracts import (
    ChapterArtifact,
    ChapterSection,
    FormulaArtifact,
    FunctionPlotLabSpec,
)
from open_notebook.course.publication_service import (
    DraftPublicationError,
    PublicationService,
)
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    ExerciseBlueprint,
    NumericGraderSpec,
    ReplaceExerciseOperation,
    ReplaceFormulaOperation,
    ReplaceLabOperation,
    ReplaceTextOperation,
)

NOW = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app)


def _artifact() -> ChapterArtifact:
    return ChapterArtifact(
        chapter_key="limits",
        purpose="Understand limits.",
        prerequisites=[],
        objectives=["Evaluate a limit."],
        sections=[
            ChapterSection(
                key="definition",
                title="Definition",
                markdown="A limit is an approached value.",
                anchor_ids=["anchor:one"],
                provenance="verbatim",
            )
        ],
        definitions=["Limit"],
        formulas=[
            FormulaArtifact(
                key="speed",
                latex="v=d/t",
                meaning="Speed is distance divided by time.",
                unit_expression="meter / second",
                oracle_unit_expression="meter / second",
                oracle_expression="d/t",
                anchor_ids=["anchor:one"],
                provenance="adapted",
            )
        ],
        citations=["anchor:one"],
        attributions={
            "purpose": {"provenance": "adapted", "anchor_ids": ["anchor:one"]},
            "prerequisites": [],
            "objectives": [{"provenance": "adapted", "anchor_ids": ["anchor:one"]}],
            "definitions": [{"provenance": "verbatim", "anchor_ids": ["anchor:one"]}],
            "misconceptions": [],
            "pitfalls": [],
            "quick_reference": [],
        },
    )


def _exercise() -> ExerciseBlueprint:
    difficulty = DifficultyVector(
        concept_count=1,
        reasoning_steps=2,
        symbolic_depth=1,
        representation_shifts=1,
        proof_burden=0,
        physics_constraints=0,
    )
    return ExerciseBlueprint(
        key="limits-core",
        chapter_key="limits",
        prompt="Evaluate the source-level limit.",
        concept_keys=["limit-laws"],
        exercise_type="generated_core",
        answer_type="numeric",
        hints=["Start from the definition."],
        source_anchor_ids=["anchor:one"],
        difficulty=difficulty,
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_core=True,
        is_gating=True,
        is_source_level=False,
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


def _draft(
    *,
    scope: DraftScope | None = None,
    revision_no: int = 3,
) -> DraftState:
    return DraftState(
        scope=scope or _scope(),
        artifact=_artifact(),
        exercises=(_exercise(),),
        revision_no=revision_no,
        revision_id=(
            f"course_draft_revision:r{revision_no}" if revision_no else None
        ),
        revision_status="validated" if revision_no else None,
    )


def test_formula_edit_invalidates_only_formula_unit_and_numeric_checks() -> None:
    service = AuthoringService(clock=lambda: NOW)
    draft = _draft()

    change = service.apply_operation(
        draft,
        ReplaceFormulaOperation(
            kind="replace_formula",
            block_key="speed",
            latex="v=2*d/t",
            anchor_ids=["anchor:one"],
        ),
        expected_revision=draft.revision_token,
    )

    assert change.revision.invalidated_checks == ("formula", "unit", "numeric")
    assert change.draft.artifact.formulas[0].latex == "v=2*d/t"
    assert change.draft.artifact.formulas[0].provenance == "adapted"
    assert change.revision.base_artifact_hash == draft.artifact_hash
    assert change.revision.artifact_hash == change.draft.artifact_hash
    assert draft.artifact.formulas[0].latex == "v=d/t"


def test_human_text_edit_keeps_explicit_provenance_and_rejects_foreign_anchors() -> None:
    service = AuthoringService(clock=lambda: NOW)
    draft = _draft()
    operation = ReplaceTextOperation(
        kind="replace_text",
        block_key="definition",
        text="A learner-friendly explanation of an approached value.",
        anchor_ids=[],
    )

    change = service.apply_operation(
        draft, operation, expected_revision=draft.revision_token
    )

    section = change.draft.artifact.sections[0]
    assert (section.provenance, section.anchor_ids) == ("pedagogical", [])
    assert change.revision.invalidated_checks == ("citation", "structure")
    with pytest.raises(DraftConflictError, match="evidence"):
        service.apply_operation(
            draft,
            operation.model_copy(update={"anchor_ids": ("anchor:foreign",)}),
            expected_revision=draft.revision_token,
        )


def test_unvalidated_edits_accumulate_every_required_check() -> None:
    service = AuthoringService(clock=lambda: NOW)
    draft = _draft()
    formula_change = service.apply_operation(
        draft,
        ReplaceFormulaOperation(
            kind="replace_formula",
            block_key="speed",
            latex="v=2*d/t",
            anchor_ids=["anchor:one"],
        ),
        expected_revision=draft.revision_token,
    )

    text_change = service.apply_operation(
        formula_change.draft,
        ReplaceTextOperation(
            kind="replace_text",
            block_key="definition",
            text="A revised source-grounded definition.",
            anchor_ids=["anchor:one"],
        ),
        expected_revision=formula_change.draft.revision_token,
    )

    assert text_change.revision.invalidated_checks == (
        "formula",
        "unit",
        "numeric",
        "citation",
        "structure",
    )


def test_v1_opaque_block_keys_remain_editable_and_ambiguous_targets_fail() -> None:
    service = AuthoringService(clock=lambda: NOW)
    legacy_artifact = _artifact().model_copy(deep=True)
    legacy_artifact.sections[0].key = "Section 1"
    legacy_draft = _draft().model_copy(update={"artifact": legacy_artifact})

    changed = service.apply_operation(
        legacy_draft,
        ReplaceTextOperation(
            kind="replace_text",
            block_key="Section 1",
            text="Updated legacy section content.",
            anchor_ids=["anchor:one"],
        ),
        expected_revision=legacy_draft.revision_token,
    )

    assert changed.draft.artifact.sections[0].markdown == (
        "Updated legacy section content."
    )

    ambiguous_artifact = _artifact().model_copy(deep=True)
    ambiguous_artifact.sections[0].key = "purpose"
    ambiguous_draft = _draft().model_copy(update={"artifact": ambiguous_artifact})
    with pytest.raises(DraftConflictError, match="ambiguous"):
        service.apply_operation(
            ambiguous_draft,
            ReplaceTextOperation(
                kind="replace_text",
                block_key="purpose",
                text="This target must not be chosen arbitrarily.",
                anchor_ids=["anchor:one"],
            ),
            expected_revision=ambiguous_draft.revision_token,
        )


def test_draft_operations_fail_closed_for_stale_ready_or_unknown_targets() -> None:
    service = AuthoringService(clock=lambda: NOW)
    draft = _draft()
    operation = ReplaceTextOperation(
        kind="replace_text",
        block_key="definition",
        text="Updated text.",
        anchor_ids=["anchor:one"],
    )

    with pytest.raises(DraftConflictError, match="revision"):
        service.apply_operation(draft, operation, expected_revision="0" * 64)
    with pytest.raises(DraftImmutableError):
        service.apply_operation(
            _draft(scope=_scope(chapter_status="ready")),
            operation,
            expected_revision=_draft(scope=_scope(chapter_status="ready")).revision_token,
        )
    with pytest.raises(DraftImmutableError):
        service.apply_operation(
            _draft(scope=_scope(version_status="published")),
            operation,
            expected_revision=_draft(scope=_scope(version_status="published")).revision_token,
        )
    with pytest.raises(DraftConflictError, match="block"):
        service.apply_operation(
            draft,
            operation.model_copy(update={"block_key": "missing"}),
            expected_revision=draft.revision_token,
        )


def test_replacing_an_exercise_preserves_its_stable_identity() -> None:
    service = AuthoringService(clock=lambda: NOW)
    draft = _draft()
    replacement = _exercise().model_copy(
        update={"prompt": "Evaluate the revised source-level limit."}
    )

    change = service.apply_operation(
        draft,
        ReplaceExerciseOperation(
            kind="replace_exercise",
            block_key="limits-core",
            exercise=replacement,
        ),
        expected_revision=draft.revision_token,
    )

    assert change.draft.exercises[0].key == "limits-core"
    assert change.draft.exercises[0].prompt == replacement.prompt
    assert change.revision.invalidated_checks == (
        "unit", "numeric", "physics", "citation", "structure"
    )
    with pytest.raises(DraftConflictError, match="identity"):
        service.apply_operation(
            draft,
            ReplaceExerciseOperation(
                kind="replace_exercise",
                block_key="limits-core",
                exercise=replacement.model_copy(update={"key": "other"}),
            ),
            expected_revision=draft.revision_token,
        )


def test_targeted_validation_reports_only_invalidated_checks() -> None:
    service = AuthoringService(clock=lambda: NOW)
    draft = _draft()
    change = service.apply_operation(
        draft,
        ReplaceFormulaOperation(
            kind="replace_formula",
            block_key="speed",
            latex="not-a-valid-formula(",
            anchor_ids=["anchor:one"],
        ),
        expected_revision=draft.revision_token,
    )

    result = service.validate_draft(change.draft, change.revision)

    assert result.valid is False
    assert result.checked == ("formula", "unit", "numeric")
    assert result.findings
    assert {finding.kind for finding in result.findings} <= set(result.checked)


def test_exercise_review_findings_are_blocking_structure_findings() -> None:
    service = AuthoringService(clock=lambda: NOW)
    draft = _draft()
    replacement = _exercise().model_copy(
        update={"prompt": "Evaluate the edited source-level limit."}
    )
    change = service.apply_operation(
        draft,
        ReplaceExerciseOperation(
            kind="replace_exercise",
            block_key=replacement.key,
            exercise=replacement,
        ),
        expected_revision=draft.revision_token,
    )

    result = service.validate_draft(change.draft, change.revision)

    assert result.valid is False
    assert any(
        finding.item_key == "missing_transfer_task"
        and finding.kind == "structure"
        for finding in result.findings
    )


def test_lab_findings_are_blocking_structure_findings() -> None:
    service = AuthoringService(clock=lambda: NOW)
    lab = FunctionPlotLabSpec(
        key="limit-plot",
        title="Limit plot",
        expressions=["x"],
        domain={"x": (-2.0, 2.0)},
        anchor_ids=["anchor:one"],
        provenance="adapted",
    )
    draft = _draft().model_copy(
        update={"artifact": _artifact().model_copy(update={"labs": [lab]})}
    )
    change = service.apply_operation(
        draft,
        ReplaceLabOperation(
            kind="replace_lab",
            block_key=lab.key,
            lab_spec=lab.model_copy(update={"expressions": ["x=1"]}),
        ),
        expected_revision=draft.revision_token,
    )

    result = service.validate_draft(change.draft, change.revision)

    assert result.valid is False
    assert any(
        finding.item_key == lab.key and finding.kind == "structure"
        for finding in result.findings
    )


def _migration_sql(version: str) -> str:
    return Path(f"open_notebook/database/migrations/{version}.surrealql").read_text()


@pytest.mark.asyncio
async def test_atomic_draft_commit_rejects_a_concurrent_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("structured_drafts", "structured_drafts")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    await database.query(
        "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
    )
    for version in ("24", "25", "26"):
        await database.query(_migration_sql(version))
    artifact = _artifact().model_dump(mode="json")
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
        {"artifact": artifact},
    )
    service = AuthoringService(clock=lambda: NOW)
    scope = _scope()
    first_snapshot = await service.get_draft(scope)
    second_snapshot = await service.get_draft(scope)
    operation = ReplaceFormulaOperation(
        kind="replace_formula",
        block_key="speed",
        latex="d/t + 0",
        anchor_ids=["anchor:one"],
    )

    saved = await service.save_operation(
        first_snapshot,
        operation,
        expected_revision=first_snapshot.revision_token,
    )
    with pytest.raises(DraftConflictError):
        await service.save_operation(
            second_snapshot,
            operation.model_copy(update={"latex": "d/t + 1"}),
            expected_revision=second_snapshot.revision_token,
        )

    rows = cast(
        list[dict[str, Any]],
        await database.query(
            "SELECT revision_no, artifact_hash FROM course_draft_revision;"
        ),
    )
    chapter = cast(
        dict[str, Any],
        await database.query("SELECT artifact FROM ONLY chapter:one;"),
    )
    assert len(rows) == 1
    assert rows[0]["revision_no"] == 1
    assert rows[0]["artifact_hash"] == saved.artifact_hash
    assert chapter["artifact"]["formulas"][0]["latex"] == "d/t + 0"
    with pytest.raises(DraftPublicationError, match="validated"):
        await PublicationService().assert_draft_ready(scope)
    validation = await service.validate_current(
        saved,
        expected_revision=saved.revision_token,
    )
    persisted_revision = await database.query(
        "SELECT VALUE status FROM course_draft_revision LIMIT 1;"
    )
    assert validation.valid is True
    assert persisted_revision == ["validated"]
    await PublicationService().assert_draft_ready(scope)
    await database.close()


@pytest.mark.asyncio
async def test_publication_gate_requires_the_latest_edited_revision_to_validate() -> None:
    draft = _draft(revision_no=1)
    loader = AsyncMock(return_value=draft.model_copy(update={"revision_status": "draft"}))
    gate = PublicationService(draft_loader=loader)

    with pytest.raises(DraftPublicationError, match="validated"):
        await gate.assert_draft_ready(draft.scope)

    loader.return_value = draft.model_copy(update={"revision_status": "validated"})
    await gate.assert_draft_ready(draft.scope)


@pytest.mark.asyncio
async def test_publication_gate_preserves_unedited_v1_chapter_flow() -> None:
    draft = _draft(revision_no=0)
    gate = PublicationService(draft_loader=AsyncMock(return_value=draft))

    await gate.assert_draft_ready(draft.scope)


def _draft_response() -> dict[str, object]:
    draft = _draft(revision_no=1)
    return {
        "chapter_key": "limits",
        "chapter_status": "reviewing",
        "editable": True,
        "revision_no": 1,
        "revision_token": draft.revision_token,
        "revision_status": "draft",
        "artifact_hash": draft.artifact_hash,
        "artifact": draft.artifact.model_dump(mode="json"),
        "exercises": [item.model_dump(mode="json") for item in draft.exercises],
    }


def test_structured_draft_routes_are_strict_and_return_409_for_stale_tokens(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_draft = AsyncMock(return_value=_draft_response())
    apply = AsyncMock(return_value=_draft_response())
    validate = AsyncMock(side_effect=CourseConflictError("Draft revision token is stale."))
    monkeypatch.setattr(course_v2_service, "get_chapter_draft", get_draft)
    monkeypatch.setattr(course_v2_service, "apply_chapter_draft_operation", apply)
    monkeypatch.setattr(course_v2_service, "validate_chapter_draft", validate)

    loaded = client.get("/api/courses/course:one/chapters/limits/draft")
    injected = client.patch(
        "/api/courses/course:one/chapters/limits/draft",
        json={
            "revision_token": "a" * 64,
            "operation": {
                "kind": "replace_formula",
                "block_key": "speed",
                "latex": "v=2*d/t",
                "anchor_ids": ["anchor:one"],
            },
            "chapter_id": "chapter:foreign",
        },
    )
    saved = client.patch(
        "/api/courses/course:one/chapters/limits/draft",
        json={
            "revision_token": "a" * 64,
            "operation": {
                "kind": "replace_formula",
                "block_key": "speed",
                "latex": "v=2*d/t",
                "anchor_ids": ["anchor:one"],
            },
        },
    )
    stale = client.post(
        "/api/courses/course:one/chapters/limits/draft/validate",
        json={"revision_token": "a" * 64},
    )

    assert loaded.status_code == 200
    assert injected.status_code == 422
    assert saved.status_code == 200
    assert stale.status_code == 409
    apply.assert_awaited_once()
    assert apply.await_args is not None
    request = apply.await_args.args[2]
    assert request.operation.kind == "replace_formula"
    assert request.model_dump().get("chapter_id") is None
