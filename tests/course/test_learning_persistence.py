import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from surrealdb import AsyncSurreal

from open_notebook.course.learning_service import LearningService
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    ExerciseBlueprint,
    LearningEvent,
    NumericGraderSpec,
    TransferTaskSpec,
)
from open_notebook.course.v2_models import CourseExercise
from open_notebook.exceptions import InvalidInputError


def _migration(version: str) -> str:
    return Path(f"open_notebook/database/migrations/{version}.surrealql").read_text()


def _graded(
    event_id: str,
    exercise_key: str,
    *,
    at: datetime,
    answer: object = "4",
) -> LearningEvent:
    return LearningEvent(
        event_id=event_id,
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_key="linear",
        concept_key="linear-equations",
        exercise_key=exercise_key,
        kind="graded_correct",
        payload={
            "answer_revealed": False,
            "hints_used": 0,
            "attempt_key": f"attempt-{event_id}",
            "response_parts": [
                json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
            ],
        },
        occurred_at=at,
    )


def _blueprint(
    exercise_key: str,
    *,
    is_core: bool = False,
    is_source_level: bool = False,
    with_transfer: bool = False,
) -> ExerciseBlueprint:
    difficulty = DifficultyVector(
        concept_count=1,
        reasoning_steps=2,
        symbolic_depth=1,
        representation_shifts=0,
        proof_burden=0,
        physics_constraints=0,
    )
    return ExerciseBlueprint(
        key=exercise_key,
        chapter_key="linear",
        prompt="Solve the source-grounded linear exercise.",
        concept_keys=["linear-equations"],
        exercise_type="generated_core" if is_core else "source_practice",
        answer_type="numeric",
        source_anchor_ids=["anchor:linear"],
        source_number="3.1" if is_source_level else None,
        difficulty=difficulty,
        grader=NumericGraderSpec(kind="numeric", expected="4"),
        is_core=is_core,
        is_gating=is_core,
        is_source_level=is_source_level,
        transfer_task=(
            TransferTaskSpec(
                key=f"{exercise_key}-transfer",
                prompt="Construct a new equation with the same invariant.",
                invariant_concept_keys=("linear-equations",),
                dimensions=("inverse_or_constructive",),
                answer_type="numeric",
                difficulty=difficulty,
                grader=NumericGraderSpec(kind="numeric", expected="4"),
                anchor_ids=("anchor:linear",),
            )
            if with_transfer
            else None
        ),
    )


@pytest.mark.asyncio
async def test_default_learning_repository_is_append_only_idempotent_and_replayable(
    monkeypatch,
) -> None:
    import open_notebook.course.learning_service as learning_module
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("course_v2_learning", "course_v2_learning")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    await database.query(
        "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
    )
    for version in ("24", "25", "26"):
        await database.query(_migration(version))
    await database.query(
        """
        CREATE notebook:one SET name = 'Notebook';
        CREATE course:one SET
            title = 'Algebra', notebook = notebook:one,
            source_ids = [], primary_source_ids = [], supplement_source_ids = [],
            outline_version_id = course_version:one;
        CREATE course_version:one SET
            course = course:one, version_no = 1, status = 'published';
        CREATE course_version:old SET
            course = course:one, version_no = 0, status = 'published';
        CREATE chapter:one SET
            course_version = course_version:one, chapter_no = 1,
            chapter_key = 'linear', title = 'Linear equations', status = 'published';
        CREATE chapter:old SET
            course_version = course_version:old, chapter_no = 1,
            chapter_key = 'linear', title = 'Old linear equations', status = 'published';
        CREATE course:other SET
            title = 'Other', notebook = notebook:one,
            source_ids = [], primary_source_ids = [], supplement_source_ids = [],
            outline_version_id = course_version:other;
        CREATE course_version:other SET
            course = course:other, version_no = 1, status = 'published';
        CREATE chapter:other SET
            course_version = course_version:other, chapter_no = 1,
            chapter_key = 'linear', title = 'Other linear equations', status = 'published';
        """
    )
    start = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)
    for blueprint in (
        _blueprint("linear-core", is_core=True),
        _blueprint("linear-source", is_source_level=True),
        _blueprint("linear-reveal", is_core=True, with_transfer=True),
    ):
        await CourseExercise(
            course="course:one",
            course_version="course_version:one",
            chapter="chapter:one",
            chapter_key=blueprint.chapter_key,
            exercise_key=blueprint.key,
            blueprint=blueprint,
            source_anchor_ids=blueprint.source_anchor_ids,
            difficulty=blueprint.difficulty,
            grader=blueprint.grader,
            is_core=blueprint.is_core,
            is_gating=blueprint.is_gating,
            is_source_level=blueprint.is_source_level,
        ).save()
    core = _graded("core-ok", "linear-core", at=start)
    source = _graded(
        "source-ok",
        "linear-source",
        at=start + timedelta(minutes=1),
    )
    service = LearningService(clock=lambda: start + timedelta(minutes=2))

    position = LearningEvent(
        event_id="position-one",
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_key="linear",
        kind="reading_position",
        payload={"block_key": "example-one"},
        occurred_at=start,
    )
    assert await service.append_activity_event(position) == position
    assert await service.append_activity_event(position) == position
    assert await service.latest_reading_position("course:one", "linear") == position

    original_repo_query = learning_module.repo_query
    activity_scope_switched = False

    async def switch_scope_before_activity_commit(query, variables=None):
        nonlocal activity_scope_switched
        values = variables if isinstance(variables, dict) else {}
        content = values.get("content") or values.get("event_content")
        if (
            not activity_scope_switched
            and isinstance(content, dict)
            and content.get("event_key") == "position-race"
        ):
            activity_scope_switched = True
            await database.query(
                "UPDATE course:one SET outline_version_id = course_version:old;"
            )
        return await original_repo_query(query, variables)

    monkeypatch.setattr(
        learning_module, "repo_query", switch_scope_before_activity_commit
    )
    with pytest.raises(InvalidInputError, match="current published"):
        await service.append_activity_event(
            position.model_copy(update={"event_id": "position-race"})
        )
    monkeypatch.setattr(learning_module, "repo_query", original_repo_query)
    await database.query(
        "UPDATE course:one SET outline_version_id = course_version:one;"
    )
    assert not await database.query(
        "SELECT id FROM course_learning_event WHERE event_key = 'position-race';"
    )

    forged = _graded(
        "forged-core",
        "linear-core",
        at=start,
        answer="5",
    )
    with pytest.raises(InvalidInputError, match="grade outcome"):
        await service.append_event(forged)
    before_grades = cast(
        list[dict[str, object]],
        await database.query("SELECT event_key FROM course_learning_event;"),
    )
    assert [row["event_key"] for row in before_grades] == ["position-one"]

    cross_course = core.model_copy(
        update={
            "event_id": "cross-course",
            "course_version_id": "course_version:other",
        }
    )
    with pytest.raises(InvalidInputError, match="Course version"):
        await service.append_event(cross_course)

    stale_version = core.model_copy(
        update={
            "event_id": "stale-version",
            "course_version_id": "course_version:old",
        }
    )
    with pytest.raises(InvalidInputError, match="current published"):
        await service.append_event(stale_version)

    mastery_scope_switched = False

    async def switch_scope_before_mastery_commit(query, variables=None):
        nonlocal mastery_scope_switched
        values = variables if isinstance(variables, dict) else {}
        content = values.get("event_content")
        if (
            not mastery_scope_switched
            and isinstance(content, dict)
            and content.get("event_key") == "scope-race"
        ):
            mastery_scope_switched = True
            await database.query(
                "UPDATE course:one SET outline_version_id = course_version:old;"
            )
        return await original_repo_query(query, variables)

    monkeypatch.setattr(
        learning_module, "repo_query", switch_scope_before_mastery_commit
    )
    with pytest.raises(InvalidInputError, match="current published"):
        await service.append_event(core.model_copy(update={"event_id": "scope-race"}))
    monkeypatch.setattr(learning_module, "repo_query", original_repo_query)
    await database.query(
        "UPDATE course:one SET outline_version_id = course_version:one;"
    )
    assert not await database.query(
        "SELECT id FROM course_learning_event WHERE event_key = 'scope-race';"
    )

    results = await asyncio.gather(
        service.append_event(core),
        service.append_event(source),
    )
    replayed = await service.append_event(source)

    event_rows = cast(
        list[dict[str, object]],
        await database.query("SELECT * FROM course_learning_event;"),
    )
    mastery_rows = cast(
        list[dict[str, object]],
        await database.query("SELECT * FROM course_concept_mastery;"),
    )
    assert {result.status for result in results} <= {"practiced", "mastered"}
    assert replayed.status == "mastered"
    assert len(event_rows) == 3
    assert len(mastery_rows) == 1
    assert mastery_rows[0]["snapshot_hash"] == replayed.snapshot_hash

    snapshot_variables: dict[str, Any] = {
        "old_due": start,
        "old_hash": "b" * 64,
    }
    await database.query(
        """
        DELETE course_concept_mastery
        WHERE course = course:one AND course_version = course_version:one;
        CREATE course_concept_mastery:historical SET
            course = course:one, course_version = course_version:old,
            chapter_key = 'old-linear', concept_key = 'old-concept',
            status = 'review_due', successful_exercise_keys = [],
            unrevealed_success_count = 0, review_level = 0,
            review_due_at = $old_due, last_event_at = $old_due,
            snapshot_hash = $old_hash;
        """,
        snapshot_variables,
    )
    queue = await service.review_queue("course:one", start + timedelta(days=2))
    repaired_rows = cast(
        list[dict[str, object]],
        await database.query(
            "SELECT * FROM course_concept_mastery "
            "WHERE course_version = course_version:one;"
        ),
    )

    assert [(item.chapter_key, item.concept_key) for item in queue] == [
        ("linear", "linear-equations")
    ]
    assert repaired_rows[0]["status"] == "review_due"
    assert len(str(repaired_rows[0]["snapshot_hash"])) == 64

    await database.query(
        """
        CREATE course_version:draft SET
            course = course:one, version_no = 2, status = 'draft';
        UPDATE course:one SET outline_version_id = course_version:draft;
        """
    )
    fallback_queue = await service.review_queue("course:one", start + timedelta(days=2))
    assert [(item.chapter_key, item.concept_key) for item in fallback_queue] == [
        ("linear", "linear-equations")
    ]

    await database.query(
        "UPDATE course:one SET outline_version_id = course_version:other;"
    )
    with pytest.raises(InvalidInputError, match="does not belong"):
        await service.review_queue("course:one", start + timedelta(days=2))
    await database.query(
        "UPDATE course:one SET outline_version_id = course_version:one;"
    )

    await database.query(
        """
        UPDATE course_concept_mastery
        SET status = 'learning', snapshot_hash = $bad_hash
        WHERE course = course:one AND course_version = course_version:one;
        """,
        {"bad_hash": "0" * 64},
    )
    repair_started = asyncio.Event()
    release_repair = asyncio.Event()
    original_save = LearningService._save_mastery_if_event_count
    paused_once = False

    async def pause_first_repair(
        self: LearningService,
        mastery,
        *,
        scope,
        expected_event_count: int,
    ) -> None:
        nonlocal paused_once
        if not paused_once:
            paused_once = True
            repair_started.set()
            await release_repair.wait()
        await original_save(
            self,
            mastery,
            scope=scope,
            expected_event_count=expected_event_count,
        )

    monkeypatch.setattr(
        LearningService, "_save_mastery_if_event_count", pause_first_repair
    )
    repair_task = asyncio.create_task(
        service.review_queue("course:one", start + timedelta(days=2))
    )
    await repair_started.wait()
    first_due = start + timedelta(days=1, minutes=1)
    review = LearningEvent(
        event_id="review-concurrent",
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_key="linear",
        concept_key="linear-equations",
        exercise_key="linear-core",
        kind="review_completed",
        payload={
            "attempt_key": "attempt-review-concurrent",
            "response_parts": [json.dumps("4")],
            "correct": True,
            "answer_revealed": False,
            "hints_used": 0,
        },
        occurred_at=first_due,
    )
    append_task = asyncio.create_task(
        LearningService(clock=lambda: start + timedelta(days=2)).append_event(review)
    )
    await asyncio.sleep(0)
    assert not append_task.done()
    release_repair.set()
    repaired_queue = await repair_task
    appended_review = await append_task
    current_rows = cast(
        list[dict[str, object]],
        await database.query(
            "SELECT * FROM course_concept_mastery "
            "WHERE course_version = course_version:one;"
        ),
    )
    assert len(repaired_queue) == 1
    assert appended_review.review_level == 1
    assert current_rows[0]["review_level"] == 1
    assert current_rows[0]["snapshot_hash"] == appended_review.snapshot_hash

    monkeypatch.setattr(LearningService, "_save_mastery_if_event_count", original_save)
    await database.query("UPDATE chapter:one SET status = 'draft';")
    with pytest.raises(InvalidInputError, match="chapter"):
        await service.review_queue("course:one", start + timedelta(days=5))
    await database.query("UPDATE chapter:one SET status = 'published';")

    await database.query(
        """
        UPDATE course_concept_mastery
        SET status = 'learning', snapshot_hash = $bad_hash
        WHERE course = course:one AND course_version = course_version:one;
        """,
        {"bad_hash": "1" * 64},
    )
    scope_switched_during_repair = False

    async def switch_scope_during_repair(
        self: LearningService,
        mastery,
        *,
        scope,
        expected_event_count: int,
    ) -> None:
        nonlocal scope_switched_during_repair
        if not scope_switched_during_repair:
            scope_switched_during_repair = True
            await database.query(
                "UPDATE course:one SET outline_version_id = course_version:old;"
            )
        await original_save(
            self,
            mastery,
            scope=scope,
            expected_event_count=expected_event_count,
        )

    monkeypatch.setattr(
        LearningService,
        "_save_mastery_if_event_count",
        switch_scope_during_repair,
    )
    with pytest.raises(InvalidInputError, match="current published"):
        await service.review_queue("course:one", start + timedelta(days=5))
    monkeypatch.setattr(LearningService, "_save_mastery_if_event_count", original_save)
    await database.query(
        "UPDATE course:one SET outline_version_id = course_version:one;"
    )

    conflicting = source.model_copy(update={"exercise_key": "other-source"})
    with pytest.raises(InvalidInputError, match="other content"):
        await service.append_event(conflicting)

    reveal = LearningEvent(
        event_id="reveal-atomic",
        course_id="course:one",
        course_version_id="course_version:one",
        chapter_key="linear",
        concept_key="linear-equations",
        exercise_key="linear-reveal",
        kind="answer_revealed",
        payload={
            "attempt_key": "attempt-reveal-atomic",
            "transfer_task_key": "linear-reveal-transfer",
        },
        occurred_at=start + timedelta(days=1, minutes=2),
    )
    required = reveal.model_copy(
        update={"event_id": "required-atomic", "kind": "transfer_required"}
    )
    atomic_service = LearningService(clock=lambda: start + timedelta(days=2))
    atomic_scope_switched = False

    async def switch_scope_before_atomic_commit(query, variables=None):
        nonlocal atomic_scope_switched
        values = variables if isinstance(variables, dict) else {}
        if not atomic_scope_switched and "event_content_1" in values:
            atomic_scope_switched = True
            await database.query(
                "UPDATE course:one SET outline_version_id = course_version:old;"
            )
        return await original_repo_query(query, variables)

    monkeypatch.setattr(
        learning_module, "repo_query", switch_scope_before_atomic_commit
    )
    with pytest.raises(InvalidInputError, match="current published"):
        await atomic_service.append_reveal_events(reveal, required)
    monkeypatch.setattr(learning_module, "repo_query", original_repo_query)
    await database.query(
        "UPDATE course:one SET outline_version_id = course_version:one;"
    )
    assert not await database.query(
        "SELECT id FROM course_learning_event "
        "WHERE event_key IN ['reveal-atomic', 'required-atomic'];"
    )

    await atomic_service.append_reveal_events(reveal, required)
    await atomic_service.append_reveal_events(reveal, required)
    atomic_rows = cast(
        list[dict[str, object]],
        await database.query(
            "SELECT event_key FROM course_learning_event "
            "WHERE event_key IN ['reveal-atomic', 'required-atomic'];"
        ),
    )
    assert {row["event_key"] for row in atomic_rows} == {
        "reveal-atomic", "required-atomic"
    }

    await database.close()
