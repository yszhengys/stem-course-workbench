"""Learner delivery must never preload grading secrets or skip learning gates."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from api.course_service import CourseConflictError, CourseService
from api.course_v2_service import CourseV2Service
from api.models import (
    CourseExerciseHintRequest,
    CourseExerciseRevealRequest,
    CourseLearnerNoteCreateRequest,
    CourseTransferGradeRequest,
)
from open_notebook.course.evidence_service import EvidenceService, EvidenceSourceAsset
from open_notebook.course.learning_service import LearningService
from open_notebook.course.models import (
    Chapter,
    CourseEvidenceAnchor,
    CourseNote,
    CourseVersion,
)
from open_notebook.course.v2_contracts import (
    ConceptMastery,
    DifficultyVector,
    ExerciseBlueprint,
    LearningEvent,
    NumericGraderSpec,
)
from open_notebook.course.v2_models import CourseExercise
from open_notebook.exceptions import InvalidInputError

NOW = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)


def _blueprint(
    *, hints: tuple[str, ...] | None = None
) -> ExerciseBlueprint:
    return ExerciseBlueprint(
        key="core-1",
        chapter_key="linear",
        prompt="Solve the source-grounded exercise.",
        concept_keys=("linear-equations", "equation-balance"),
        exercise_type="generated_core",
        answer_type="numeric",
        source_anchor_ids=("anchor:linear",),
        difficulty=DifficultyVector(
            concept_count=2,
            reasoning_steps=2,
            symbolic_depth=1,
            representation_shifts=0,
            proof_burden=0,
            physics_constraints=0,
        ),
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        hints=hints or (
            "Identify the invariant relationship.",
            "Represent both sides with one equation.",
            "Isolate the unknown without changing the balance.",
            "Check the result by substitution.",
        ),
        is_core=True,
        is_gating=True,
        transfer_task={
            "key": "core-1-transfer",
            "prompt": "Apply the invariant in a changed representation.",
            "invariant_concept_keys": ["linear-equations"],
            "dimensions": ["representation"],
            "answer_type": "numeric",
            "difficulty": {
                "concept_count": 2,
                "reasoning_steps": 2,
                "symbolic_depth": 1,
                "representation_shifts": 1,
                "proof_burden": 0,
                "physics_constraints": 0,
            },
            "grader": {"kind": "numeric", "expected": "8"},
            "anchor_ids": ["anchor:linear"],
        },
    )


def _exercise(*, hints: tuple[str, ...] | None = None) -> CourseExercise:
    blueprint = _blueprint(hints=hints)
    return CourseExercise(
        id="course_exercise:one",
        course="course:abc",
        course_version="course_version:published",
        chapter="chapter:published",
        chapter_key=blueprint.chapter_key,
        exercise_key=blueprint.key,
        blueprint=blueprint,
        source_anchor_ids=blueprint.source_anchor_ids,
        difficulty=blueprint.difficulty,
        grader=blueprint.grader,
        is_core=blueprint.is_core,
        is_gating=blueprint.is_gating,
        is_source_level=blueprint.is_source_level,
    )


def _chapter_artifact() -> dict[str, object]:
    attribution = {"provenance": "adapted", "anchor_ids": ["anchor:linear"]}
    return {
        "chapter_key": "linear",
        "purpose": "Learn a source-grounded invariant.",
        "prerequisites": [],
        "objectives": ["Solve a linear equation."],
        "sections": [{
            "key": "definition",
            "title": "Definition",
            "markdown": "Keep both sides balanced.",
            "anchor_ids": ["anchor:linear"],
            "provenance": "adapted",
        }],
        "definitions": [],
        "formulas": [{
            "key": "balance",
            "latex": "x+1=5",
            "meaning": "A balanced equation.",
            "anchor_ids": ["anchor:linear"],
            "provenance": "adapted",
            "unit_expression": None,
            "oracle_unit_expression": None,
            "oracle_expression": "x+1-5",
            "oracle_substitutions": {"x": 4},
        }],
        "worked_examples": [{
            "key": "worked-1",
            "prompt": "Solve x+1=5.",
            "steps": ["Subtract one."],
            "answer": "x=4",
            "anchor_ids": ["anchor:linear"],
            "provenance": "adapted",
            "oracle_expression": "4+1",
            "oracle_values": {},
            "oracle_answer": 5,
            "unit_expression": None,
            "oracle_unit_expression": None,
        }],
        "labs": [],
        "misconceptions": [],
        "pitfalls": [],
        "exercises": [{
            "key": "core-1",
            "prompt": "Solve it.",
            "difficulty": "core",
            "hints": ["Secret hint one", "Secret hint two"],
            "answer": "Secret full answer",
            "transfer_task": "Secret transfer answer path",
            "anchor_ids": ["anchor:linear"],
            "provenance": "adapted",
            "oracle_expression": "2+2",
            "oracle_values": {},
            "oracle_answer": 4,
        }],
        "quick_reference": [],
        "citations": ["anchor:linear"],
        "attributions": {
            "purpose": attribution,
            "prerequisites": [],
            "objectives": [attribution],
            "definitions": [],
            "misconceptions": [],
            "pitfalls": [],
            "quick_reference": [],
        },
        "physics_checks": [],
    }


def _scope() -> tuple[CourseVersion, Chapter]:
    return (
        CourseVersion(
            id="course_version:published",
            course="course:abc",
            version_no=2,
            status="published",
        ),
        Chapter(
            id="chapter:published",
            course_version="course_version:published",
            chapter_no=1,
            chapter_key="linear",
            title="Linear equations",
            status="published",
            artifact=_chapter_artifact(),
        ),
    )


def _mastery() -> ConceptMastery:
    return ConceptMastery(
        course_id="course:abc",
        course_version_id="course_version:published",
        chapter_key="linear",
        concept_key="linear-equations",
        status="practiced",
        successful_exercise_keys=("core-1",),
        unrevealed_success_count=1,
        review_level=0,
        review_due_at=None,
        last_event_at=NOW,
        snapshot_hash="a" * 64,
    )


def test_learning_snapshots_change_with_source_grounded_input_hash() -> None:
    version, chapter = _scope()
    service = CourseV2Service()
    first = version.model_copy(update={"input_hash": "a" * 64})
    changed = version.model_copy(update={"input_hash": "b" * 64})

    assert service._chapter_snapshot_token(
        "course:abc", first, chapter
    ) != service._chapter_snapshot_token("course:abc", changed, chapter)
    assert service._exercise_snapshot_token(
        "course:abc", first, _exercise()
    ) != service._exercise_snapshot_token("course:abc", changed, _exercise())


def _hint_event() -> LearningEvent:
    return LearningEvent(
        event_id="hint-event",
        course_id="course:abc",
        course_version_id="course_version:published",
        chapter_key="linear",
        concept_key="linear-equations",
        exercise_key="core-1",
        kind="hint_viewed",
        payload={"attempt_key": "attempt-one", "hint_index": 1},
        occurred_at=NOW,
    )


def _reveal_event() -> LearningEvent:
    return LearningEvent(
        event_id="reveal-event",
        course_id="course:abc",
        course_version_id="course_version:published",
        chapter_key="linear",
        concept_key="linear-equations",
        exercise_key="core-1",
        kind="answer_revealed",
        payload={
            "attempt_key": "attempt-one",
            "transfer_task_key": "core-1-transfer",
        },
        occurred_at=NOW,
    )


def _required_event() -> LearningEvent:
    return LearningEvent(
        event_id="required-event",
        course_id="course:abc",
        course_version_id="course_version:published",
        chapter_key="linear",
        concept_key="linear-equations",
        exercise_key="core-1",
        kind="transfer_required",
        payload={
            "attempt_key": "attempt-one",
            "transfer_task_key": "core-1-transfer",
        },
        occurred_at=NOW,
    )


@pytest.mark.asyncio
async def test_learning_service_commits_reveal_and_transfer_as_one_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = LearningService(clock=lambda: NOW)
    scope = SimpleNamespace(
        chapter_id="chapter:published",
        outline_version_id="course_version:published",
        outline_version_status="published",
        uses_published_pointer=True,
    )
    monkeypatch.setattr(
        LearningService, "_resolve_scope", AsyncMock(return_value=scope)
    )
    monkeypatch.setattr(
        LearningService, "_load_event_records", AsyncMock(return_value=())
    )
    monkeypatch.setattr(
        LearningService, "_load_event_by_key", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        LearningService,
        "_load_exercise_records",
        AsyncMock(return_value=(_exercise().blueprint,)),
    )
    commit = AsyncMock()
    monkeypatch.setattr(LearningService, "_commit_events_and_mastery", commit)

    mastery = await service.append_reveal_events(
        _reveal_event(), _required_event()
    )

    assert mastery.concept_key == "linear-equations"
    commit.assert_awaited_once()
    assert commit.await_args is not None
    committed_events = commit.await_args.args[0]
    assert [event.kind for event in committed_events] == [
        "answer_revealed", "transfer_required"
    ]
    assert commit.await_args.kwargs["insert_events"] == (True, True)


@pytest.mark.asyncio
async def test_learning_chapter_projection_omits_build_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=(version, chapter)),
    )
    response = await CourseV2Service().get_learning_chapter(
        "course:abc", "linear"
    )
    payload = response.model_dump(mode="json")
    encoded = response.model_dump_json()

    assert payload["course_version_id"] == "course_version:published"
    assert payload["artifact"]["worked_examples"][0]["answer"] == "x=4"
    assert "exercises" not in payload["artifact"]
    assert "attributions" not in payload["artifact"]
    assert "physics_checks" not in payload["artifact"]
    assert "oracle" not in encoded
    assert "Secret" not in encoded


@pytest.mark.asyncio
async def test_hint_endpoint_releases_only_one_recorded_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    exercise = _exercise()
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=(version, chapter)),
    )
    monkeypatch.setattr(
        CourseService, "get_current_exercise", AsyncMock(return_value=exercise)
    )
    monkeypatch.setattr(
        CourseService, "get_learning_event", AsyncMock(return_value=None)
    )
    service = CourseV2Service()
    snapshot = service._exercise_snapshot_token(
        "course:abc", version, exercise
    )
    append = AsyncMock(
        return_value=SimpleNamespace(event=_hint_event(), mastery=_mastery())
    )
    monkeypatch.setattr(service, "append_learning_event", append)

    response = await service.next_hint(
        "course:abc",
        "core-1",
        CourseExerciseHintRequest(
            snapshot_token=snapshot,
            idempotency_key="hint-one",
            chapter_key="linear",
            concept_key="linear-equations",
            attempt_key="attempt-one",
            hint_index=1,
        ),
    )

    assert response.hint_index == 1
    assert response.hint == "Identify the invariant relationship."
    assert "Represent both sides" not in response.model_dump_json()
    assert append.await_args is not None
    request = append.await_args.args[1]
    assert request.kind == "hint_viewed"
    assert request.payload.hint_index == 1


@pytest.mark.asyncio
async def test_reveal_records_transfer_gate_before_returning_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    exercise = _exercise()
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=(version, chapter)),
    )
    monkeypatch.setattr(
        CourseService, "get_current_exercise", AsyncMock(return_value=exercise)
    )
    append_reveal_events = AsyncMock(return_value=_mastery())
    monkeypatch.setattr(
        CourseService, "get_learning_event", AsyncMock(return_value=None)
    )
    learning = cast(
        LearningService,
        SimpleNamespace(append_reveal_events=append_reveal_events),
    )
    service = CourseV2Service(learning_service=learning, clock=lambda: NOW)
    snapshot = service._exercise_snapshot_token(
        "course:abc", version, exercise
    )
    response = await service.reveal_answer(
        "course:abc",
        "core-1",
        CourseExerciseRevealRequest(
            snapshot_token=snapshot,
            idempotency_key="reveal-one",
            chapter_key="linear",
            concept_key="linear-equations",
            attempt_key="attempt-one",
        ),
    )

    assert response.answer == "4"
    assert response.transfer is not None
    assert response.transfer.key == "core-1-transfer"
    assert "grader" not in response.model_dump_json()
    append_reveal_events.assert_awaited_once()
    assert append_reveal_events.await_args is not None
    revealed, required = append_reveal_events.await_args.args
    assert revealed.kind == "answer_revealed"
    assert required is not None and required.kind == "transfer_required"
    assert revealed.occurred_at == required.occurred_at == NOW
    assert [event.kind for event in response.events] == [
        "answer_revealed", "transfer_required"
    ]


@pytest.mark.asyncio
async def test_transfer_is_server_graded_and_only_success_appends_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    exercise = _exercise()
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=(version, chapter)),
    )
    monkeypatch.setattr(
        CourseService, "get_current_exercise", AsyncMock(return_value=exercise)
    )
    monkeypatch.setattr(
        CourseService, "get_learning_event", AsyncMock(return_value=None)
    )
    append_event = AsyncMock(return_value=_mastery())
    learning = cast(
        LearningService,
        SimpleNamespace(append_event=append_event),
    )
    service = CourseV2Service(learning_service=learning, clock=lambda: NOW)
    snapshot = service._exercise_snapshot_token(
        "course:abc", version, exercise
    )
    request = CourseTransferGradeRequest(
        snapshot_token=snapshot,
        chapter_key="linear",
        concept_key="linear-equations",
        source_attempt_key="attempt-one",
        attempt_key="transfer-attempt-one",
        transfer_task_key="core-1-transfer",
        answer="8",
    )

    correct = await service.grade_transfer("course:abc", "core-1", request)
    assert correct.grade.correct is True
    assert correct.mastery == _mastery()
    assert append_event.await_args is not None
    event = append_event.await_args.args[0]
    assert event.kind == "transfer_completed"
    assert event.payload.source_attempt_key == "attempt-one"

    append_event.reset_mock()
    incorrect = await service.grade_transfer(
        "course:abc",
        "core-1",
        request.model_copy(update={
            "attempt_key": "transfer-attempt-two",
            "answer": "7",
        }),
    )
    assert incorrect.grade.correct is False
    assert incorrect.mastery is None
    append_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_transfer_retry_reuses_the_persisted_event_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    exercise = _exercise()
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=(version, chapter)),
    )
    monkeypatch.setattr(
        CourseService, "get_current_exercise", AsyncMock(return_value=exercise)
    )
    stored: LearningEvent | None = None

    async def get_event(_course_id: str, _event_key: str) -> LearningEvent | None:
        return stored

    async def append_event(event: LearningEvent) -> ConceptMastery:
        nonlocal stored
        if stored is None:
            stored = event
        return _mastery()

    append = AsyncMock(side_effect=append_event)
    learning = cast(LearningService, SimpleNamespace(append_event=append))
    moments = iter((NOW, NOW.replace(second=2), NOW.replace(second=4)))
    service = CourseV2Service(learning_service=learning, clock=lambda: next(moments))
    monkeypatch.setattr(CourseService, "get_learning_event", get_event)
    snapshot = service._exercise_snapshot_token("course:abc", version, exercise)
    request = CourseTransferGradeRequest(
        snapshot_token=snapshot,
        chapter_key="linear",
        concept_key="linear-equations",
        source_attempt_key="attempt-one",
        attempt_key="transfer-attempt-retry",
        transfer_task_key="core-1-transfer",
        answer="8",
    )

    first = await service.grade_transfer("course:abc", "core-1", request)
    second = await service.grade_transfer("course:abc", "core-1", request)

    assert first.event_key == second.event_key
    assert append.await_args_list[0].args[0] == append.await_args_list[1].args[0]
    with pytest.raises(InvalidInputError, match="already graded"):
        await service.grade_transfer(
            "course:abc",
            "core-1",
            request.model_copy(update={"answer": "8.0"}),
        )


@pytest.mark.asyncio
async def test_mastery_reload_projects_pending_transfer_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    pending = {
        "chapter_key": "linear",
        "concept_key": "linear-equations",
        "exercise_key": "core-1",
        "source_attempt_key": "attempt-one",
        "transfer_task_key": "core-1-transfer",
    }
    row = {
        "course": "course:abc",
        "course_version": "course_version:published",
        "chapter_key": "linear",
        "concept_key": "linear-equations",
        "status": "practiced",
        "successful_exercise_keys": ["core-1"],
        "unrevealed_success_count": 0,
        "pending_transfers": [pending],
        "review_level": 0,
        "review_due_at": None,
        "last_event_at": NOW,
        "snapshot_hash": "a" * 64,
    }
    monkeypatch.setattr(
        CourseService,
        "list_current_published_chapters",
        AsyncMock(return_value=(version, (chapter,))),
    )
    monkeypatch.setattr("api.course_service.repo_query", AsyncMock(return_value=[row]))
    monkeypatch.setattr(
        CourseService, "confirm_current_published_scope", AsyncMock()
    )

    _current, masteries = await CourseService.list_current_masteries("course:abc")

    assert [item.model_dump(mode="json") for item in masteries[0].pending_transfers] == [
        pending
    ]


@pytest.mark.asyncio
async def test_learning_service_reload_projects_pending_transfer_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = {
        "chapter_key": "linear",
        "concept_key": "linear-equations",
        "exercise_key": "core-1",
        "source_attempt_key": "attempt-one",
        "transfer_task_key": "core-1-transfer",
    }
    row = {
        "course": "course:abc",
        "course_version": "course_version:published",
        "chapter_key": "linear",
        "concept_key": "linear-equations",
        "status": "practiced",
        "successful_exercise_keys": ["core-1"],
        "unrevealed_success_count": 0,
        "pending_transfers": [pending],
        "review_level": 0,
        "review_due_at": None,
        "last_event_at": NOW,
        "snapshot_hash": "a" * 64,
    }
    monkeypatch.setattr(
        "open_notebook.course.learning_service.repo_query",
        AsyncMock(return_value=[row]),
    )

    masteries = await LearningService()._load_mastery_records(
        "course:abc", "course_version:published"
    )

    assert [item.model_dump(mode="json") for item in masteries[0].pending_transfers] == [
        pending
    ]


def test_learning_actions_reject_client_oracles_and_record_ids() -> None:
    with pytest.raises(ValueError):
        CourseExerciseHintRequest.model_validate({
            "idempotency_key": "hint-one",
            "chapter_key": "linear",
            "concept_key": "linear-equations",
            "attempt_key": "attempt-one",
            "hint_index": 1,
            "grader": {"kind": "numeric", "expected": "4"},
        })
    with pytest.raises(ValueError):
        CourseTransferGradeRequest.model_validate({
            "chapter_key": "linear",
            "concept_key": "linear-equations",
            "source_attempt_key": "attempt-one",
            "attempt_key": "transfer-attempt-one",
            "transfer_task_key": "core-1-transfer",
            "answer": "8",
            "course_version_id": "course_version:foreign",
        })


@pytest.mark.asyncio
async def test_hint_index_outside_authored_layers_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=(version, chapter)),
    )
    exercise = _exercise(hints=("One", "Two", "Three"))
    monkeypatch.setattr(
        CourseService, "get_current_exercise", AsyncMock(return_value=exercise)
    )
    service = CourseV2Service()
    snapshot = service._exercise_snapshot_token(
        "course:abc", version, exercise
    )
    append = AsyncMock()
    monkeypatch.setattr(service, "append_learning_event", append)

    with pytest.raises(InvalidInputError, match="hint"):
        await service.next_hint(
            "course:abc",
            "core-1",
            CourseExerciseHintRequest(
                snapshot_token=snapshot,
                idempotency_key="hint-four",
                chapter_key="linear",
                concept_key="linear-equations",
                attempt_key="attempt-one",
                hint_index=4,
            ),
        )
    append.assert_not_awaited()


@pytest.mark.asyncio
async def test_learning_sources_are_current_chapter_citations_with_verified_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=(version, chapter)),
    )
    monkeypatch.setattr(
        CourseService,
        "list_current_exercises",
        AsyncMock(return_value=(version, ())),
    )
    anchor = CourseEvidenceAnchor(
        id="course_evidence_anchor:linear",
        course="course:abc",
        source="source:linear",
        anchor_id="anchor:linear",
        locator={
            "source_id": "source:linear",
            "kind": "pdf_page",
            "index": 4,
            "block_key": "exercise-4",
            "quote": "Solve the balanced equation.",
            "content_sha256": "d" * 64,
            "bbox": (0.1, 0.2, 0.8, 0.4),
        },
        quote_sha256="e" * 64,
        source_role="PRIMARY",
        is_current=True,
    )
    owned = AsyncMock(return_value=((
        anchor,
        SimpleNamespace(),
        EvidenceSourceAsset(
            path=Path("/private/course.pdf"),
            filename="course.pdf",
            kind="pdf",
        ),
        "d" * 64,
    ),))
    monkeypatch.setattr(CourseService, "_owned_evidence_assets", owned)
    confirm = AsyncMock()
    monkeypatch.setattr(
        CourseService, "confirm_current_published_scope", confirm
    )

    response = await CourseV2Service().list_learning_sources(
        "course:abc", "linear"
    )

    assert response.snapshot_token
    assert len(response.sources) == 1
    assert response.sources[0].filename == "course.pdf"
    assert response.sources[0].index == 4
    assert response.sources[0].quote == "Solve the balanced equation."
    owned.assert_awaited_once_with("course:abc", ("anchor:linear",))
    confirm.assert_awaited_once_with(
        "course:abc",
        "course_version:published",
        {"linear": "chapter:published"},
        exact=False,
    )


@pytest.mark.asyncio
async def test_learning_source_scope_includes_attributions_and_exercise_bank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    artifact = _chapter_artifact()
    cast(dict[str, object], artifact["attributions"])["purpose"] = {
        "provenance": "adapted",
        "anchor_ids": ["anchor:purpose-only"],
    }
    chapter = chapter.model_copy(update={"artifact": artifact})
    base = _blueprint()
    assert base.transfer_task is not None
    blueprint = base.model_copy(update={
        "source_anchor_ids": ("anchor:exercise-only",),
        "transfer_task": base.transfer_task.model_copy(
            update={"anchor_ids": ("anchor:transfer-only",)}
        ),
    })
    exercise = CourseExercise(
        id="course_exercise:scope",
        course="course:abc",
        course_version="course_version:published",
        chapter="chapter:published",
        chapter_key=blueprint.chapter_key,
        exercise_key=blueprint.key,
        blueprint=blueprint,
        source_anchor_ids=blueprint.source_anchor_ids,
        difficulty=blueprint.difficulty,
        grader=blueprint.grader,
        is_core=blueprint.is_core,
        is_gating=blueprint.is_gating,
        is_source_level=blueprint.is_source_level,
    )
    monkeypatch.setattr(
        CourseService,
        "list_current_exercises",
        AsyncMock(return_value=(version, (exercise,))),
    )

    anchor_ids = await CourseV2Service()._chapter_anchor_ids(
        "course:abc", chapter, "linear"
    )

    assert "anchor:purpose-only" in anchor_ids
    assert "anchor:exercise-only" in anchor_ids
    assert "anchor:transfer-only" in anchor_ids


@pytest.mark.asyncio
async def test_bulk_owned_evidence_hashes_each_unique_source_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.course_service as course_service_module

    source_hash = "d" * 64
    def make_anchor(index: int) -> CourseEvidenceAnchor:
        quote = f"Grounded quote {index}."
        quote_hash = EvidenceService.quote_sha256(quote)
        anchor_id = EvidenceService.deterministic_anchor_id(
            course_id="course:abc",
            source_id="source:book",
            source_sha256=source_hash,
            kind="pdf_page",
            index=index + 1,
            block_key=f"block-{index}",
            quote_sha256=quote_hash,
        )
        return CourseEvidenceAnchor(
            id=f"course_evidence_anchor:item-{index}",
            course="course:abc",
            source="source:book",
            anchor_id=anchor_id,
            locator={
                "source_id": "source:book",
                "kind": "pdf_page",
                "index": index + 1,
                "block_key": f"block-{index}",
                "quote": quote,
                "content_sha256": source_hash,
                "bbox": None,
            },
            quote_sha256=quote_hash,
            source_role="PRIMARY",
            is_current=True,
        )

    anchors = tuple(make_anchor(index) for index in range(100))
    get_course = AsyncMock(
        return_value=SimpleNamespace(source_ids=["source:book"])
    )
    query = AsyncMock(
        return_value=[anchor.model_dump(mode="json") for anchor in anchors]
    )
    get_source = AsyncMock(
        return_value=SimpleNamespace(
            asset=SimpleNamespace(file_path="/private/book.pdf")
        )
    )
    hash_calls: list[Path] = []

    def hash_once(path: Path) -> str:
        hash_calls.append(path)
        return source_hash

    monkeypatch.setattr(CourseService, "get_course", get_course)
    monkeypatch.setattr(course_service_module, "repo_query", query)
    monkeypatch.setattr(course_service_module, "_typed_get", get_source)
    monkeypatch.setattr(
        EvidenceService,
        "validate_local_source_file",
        lambda _self, value: (Path(value), "pdf"),
    )
    monkeypatch.setattr(EvidenceService, "sha256_file", staticmethod(hash_once))

    resolved = await CourseService._owned_evidence_assets(
        "course:abc",
        tuple(anchor.anchor_id for anchor in anchors),
    )

    assert len(resolved) == 100
    assert hash_calls == [Path("/private/book.pdf")]
    get_course.assert_awaited_once_with("course:abc")
    get_source.assert_awaited_once_with(
        course_service_module.Source, "source:book", "source"
    )


@pytest.mark.asyncio
async def test_learning_notes_are_version_scoped_and_stale_create_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter = _scope()
    monkeypatch.setattr(
        CourseService,
        "resolve_current_published_chapter",
        AsyncMock(return_value=(version, chapter)),
    )
    current = CourseNote(
        id="course_note:current",
        course="course:abc",
        chapter="chapter:published",
        chapter_key="linear",
        block_key="definition",
        content="Current note",
    )
    stale = CourseNote(
        id="course_note:stale",
        course="course:abc",
        chapter="chapter:old",
        chapter_key="linear",
        block_key="definition",
        content="Old note",
    )
    monkeypatch.setattr(
        CourseNote,
        "list_by_course",
        AsyncMock(return_value=[current, stale]),
    )
    confirm = AsyncMock()
    monkeypatch.setattr(
        CourseService, "confirm_current_published_scope", confirm
    )
    service = CourseV2Service()

    listed = await service.list_learning_notes("course:abc", "linear")
    assert [note.content for note in listed.notes] == ["Current note"]

    snapshot = service._chapter_snapshot_token("course:abc", version, chapter)
    saved: list[CourseNote] = []

    async def save(note: CourseNote) -> None:
        note.id = "course_note:new"
        saved.append(note)

    delete = AsyncMock()
    monkeypatch.setattr(CourseNote, "save", save)
    monkeypatch.setattr(CourseNote, "delete", delete)
    confirm.side_effect = CourseConflictError("published version changed")

    with pytest.raises(CourseConflictError, match="changed"):
        await service.create_learning_note(
            "course:abc",
            "linear",
            CourseLearnerNoteCreateRequest(
                snapshot_token=snapshot,
                block_key="definition",
                content="Do not attach to a new version",
            ),
        )
    assert len(saved) == 1
    delete.assert_awaited_once()
