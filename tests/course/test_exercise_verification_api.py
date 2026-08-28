from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from surrealdb import AsyncSurreal

from api.course_service import (
    CourseConflictError,
    CourseImmutableError,
    CourseService,
)
from api.course_v2_service import CourseV2Service, course_v2_service
from api.models import (
    CourseExerciseVerificationRequest,
    ExerciseVerificationResponse,
)
from open_notebook.course.models import Chapter, Course, CourseVersion
from open_notebook.course.v2_contracts import (
    DifficultyVector,
    ExerciseBlueprint,
    ExerciseVerification,
    NumericGraderSpec,
)
from open_notebook.course.v2_models import CourseExercise

NOW = datetime(2026, 8, 29, 8, 30, tzinfo=timezone.utc)


def _migration(version: str) -> str:
    return Path(f"open_notebook/database/migrations/{version}.surrealql").read_text(
        encoding="utf-8"
    )


@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app)


def _records() -> tuple[CourseVersion, Chapter, CourseExercise]:
    version = CourseVersion(
        id="course_version:current",
        course="course:one",
        version_no=2,
        status="generating",
        outline_hash="a" * 64,
        input_hash="b" * 64,
    )
    chapter = Chapter(
        id="chapter:current",
        course_version="course_version:current",
        chapter_no=1,
        chapter_key="motion",
        title="Motion",
        status="ready",
    )
    blueprint = ExerciseBlueprint(
        key="motion-core",
        chapter_key="motion",
        prompt="Find the acceleration.",
        concept_keys=("acceleration",),
        exercise_type="generated_core",
        answer_type="numeric",
        source_anchor_ids=("anchor:motion",),
        difficulty=DifficultyVector(
            concept_count=1,
            reasoning_steps=2,
            symbolic_depth=1,
            representation_shifts=0,
            proof_burden=0,
            physics_constraints=1,
        ),
        grader=NumericGraderSpec(kind="numeric", expected="9.8"),
        is_core=True,
        is_gating=True,
        transfer_task={
            "key": "motion-transfer",
            "prompt": "Apply the same invariant to a new motion.",
            "invariant_concept_keys": ["acceleration"],
            "dimensions": ["math_physics_context"],
            "answer_type": "numeric",
            "difficulty": {
                "concept_count": 1,
                "reasoning_steps": 2,
                "symbolic_depth": 1,
                "representation_shifts": 1,
                "proof_burden": 0,
                "physics_constraints": 1,
            },
            "grader": {"kind": "numeric", "expected": "4.9"},
            "anchor_ids": ["anchor:motion"],
        },
    )
    exercise = CourseExercise(
        id="course_exercise:motion",
        course="course:one",
        course_version="course_version:current",
        chapter="chapter:current",
        chapter_key="motion",
        exercise_key="motion-core",
        blueprint=blueprint,
        source_anchor_ids=blueprint.source_anchor_ids,
        difficulty=blueprint.difficulty,
        grader=blueprint.grader,
        is_core=True,
        is_gating=True,
    )
    return version, chapter, exercise


def test_verification_route_rejects_client_supplied_grader(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    verify = AsyncMock()
    monkeypatch.setattr(course_v2_service, "verify_exercise", verify, raising=False)

    response = client.post(
        "/api/courses/course:one/chapters/motion/exercises/motion-core/verify",
        json={
            "snapshot_token": "a" * 64,
            "expected_answer_confirmation": "9.8",
            "reason": "I checked the derivation and displayed answer.",
            "grader": {"kind": "numeric", "expected": "1"},
        },
    )

    assert response.status_code == 422
    verify.assert_not_awaited()


def test_verification_route_returns_server_authored_l3_provenance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = ExerciseVerificationResponse(
        level="L3",
        method="human_review",
        anchor_ids=("anchor:motion",),
        reason="Teacher checked the derivation and displayed answer.",
        verified_at=NOW,
    )
    verify = AsyncMock(return_value=result)
    monkeypatch.setattr(course_v2_service, "verify_exercise", verify, raising=False)

    response = client.post(
        "/api/courses/course:one/chapters/motion/exercises/motion-core/verify",
        json={
            "snapshot_token": "a" * 64,
            "expected_answer_confirmation": "9.8",
            "reason": "Teacher checked the derivation and displayed answer.",
        },
    )

    assert response.status_code == 200
    assert response.json()["level"] == "L3"
    assert response.json()["method"] == "human_review"
    verify.assert_awaited_once()


@pytest.mark.asyncio
async def test_human_verification_binds_current_snapshot_and_exact_expected_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter, exercise = _records()
    service = CourseV2Service(clock=lambda: NOW)
    snapshot = service._exercise_snapshot_token("course:one", version, exercise)
    load = AsyncMock(return_value=(version, chapter, exercise))

    async def persist(*_args, verification, **_kwargs):
        exercise.verification = verification
        return exercise

    save = AsyncMock(side_effect=persist)
    monkeypatch.setattr(CourseService, "get_current_authoring_exercise", load)
    monkeypatch.setattr(CourseService, "set_exercise_verification", save)

    result = await service.verify_exercise(
        "course:one",
        "motion",
        "motion-core",
        CourseExerciseVerificationRequest(
            snapshot_token=snapshot,
            expected_answer_confirmation="9.8",
            reason="Teacher checked the derivation and displayed answer.",
        ),
    )

    assert result.level == "L3"
    assert result.method == "human_review"
    assert result.verified_at == NOW
    assert result.reason == "Teacher checked the derivation and displayed answer."
    assert result.anchor_ids == ("anchor:motion",)
    save.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot", "answer", "message"),
    [
        ("f" * 64, "9.8", "snapshot changed"),
        (None, "9.81", "expected answer changed"),
    ],
)
async def test_human_verification_rejects_stale_or_mismatched_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: str | None,
    answer: str,
    message: str,
) -> None:
    version, chapter, exercise = _records()
    service = CourseV2Service(clock=lambda: NOW)
    current = service._exercise_snapshot_token("course:one", version, exercise)
    monkeypatch.setattr(
        CourseService,
        "get_current_authoring_exercise",
        AsyncMock(return_value=(version, chapter, exercise)),
    )
    save = AsyncMock()
    monkeypatch.setattr(CourseService, "set_exercise_verification", save)

    with pytest.raises(CourseConflictError, match=message):
        await service.verify_exercise(
            "course:one",
            "motion",
            "motion-core",
            CourseExerciseVerificationRequest(
                snapshot_token=snapshot or current,
                expected_answer_confirmation=answer,
                reason="Teacher checked the displayed answer.",
            ),
        )

    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_published_current_version_cannot_be_promoted_to_l3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version, chapter, _exercise = _records()
    version.status = "published"
    course = Course(
        id="course:one",
        title="Mechanics",
        notebook="notebook:one",
        status="ready",
        outline_version_id="course_version:current",
    )
    monkeypatch.setattr(
        "api.course_service._current_chapter_records",
        AsyncMock(return_value=(course, version, chapter)),
    )
    query = AsyncMock()
    monkeypatch.setattr("api.course_service.repo_query", query)

    with pytest.raises(CourseImmutableError, match="version is immutable"):
        await CourseService.get_current_authoring_exercise(
            "course:one", "motion", "motion-core"
        )

    query.assert_not_awaited()


@pytest_asyncio.fixture
async def verification_database(monkeypatch: pytest.MonkeyPatch):
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("exercise_verification", "exercise_verification")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    monkeypatch.setattr("api.course_service.repo_query", repository.repo_query)
    await database.query(
        "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
    )
    for migration_version in ("24", "25", "26", "27"):
        await database.query(_migration(migration_version))

    version, chapter, exercise = _records()
    await repository.repo_query("CREATE notebook:one CONTENT { name: 'Course' };")
    await repository.repo_query(
        """
        CREATE course:one CONTENT {
            title: 'Mechanics', notebook: notebook:one, status: 'generating',
            language: 'zh-CN', source_ids: [], primary_source_ids: [],
            supplement_source_ids: [], outline_version_id: course_version:current
        };
        CREATE course_version:current CONTENT {
            course: course:one, version_no: 2, status: 'generating',
            outline_hash: $outline_hash, input_hash: $input_hash
        };
        CREATE chapter:current CONTENT {
            course_version: course_version:current, chapter_no: 1,
            chapter_key: 'motion', version_no: 1, title: 'Motion',
            status: 'ready'
        };
        """,
        {
            "outline_hash": version.outline_hash,
            "input_hash": version.input_hash,
        },
    )
    content = exercise._prepare_save_data()
    content.pop("id", None)
    content.pop("created", None)
    content.pop("updated", None)
    await repository.repo_query(
        "CREATE course_exercise:motion CONTENT $content;",
        {"content": content},
    )

    yield repository
    await database.close()


@pytest.mark.asyncio
async def test_l3_verification_write_is_atomic_and_rejects_a_stale_record(
    verification_database,
) -> None:
    repository = verification_database
    version_row = await repository.repo_query(
        "SELECT * FROM course_version:current;"
    )
    chapter_row = await repository.repo_query("SELECT * FROM chapter:current;")
    exercise_row = await repository.repo_query(
        "SELECT * FROM course_exercise:motion;"
    )
    version = CourseVersion(**version_row[0])
    chapter = Chapter(**chapter_row[0])
    exercise = CourseExercise(**exercise_row[0])
    verification = ExerciseVerification(
        level="L3",
        method="human_review",
        anchor_ids=("anchor:motion",),
        reason="Teacher checked the derivation and displayed answer.",
        verified_at=NOW,
    )

    persisted = await CourseService.set_exercise_verification(
        course_id="course:one",
        version=version,
        chapter=chapter,
        exercise=exercise,
        verification=verification,
    )

    assert persisted.verification == verification
    stored = CourseExercise(
        **(
            await repository.repo_query(
                "SELECT * FROM course_exercise:motion;"
            )
        )[0]
    )
    assert stored.verification == verification

    await repository.repo_query(
        "UPDATE course_exercise:motion SET verification = $verification;",
        {
            "verification": ExerciseVerification(
                level="L2",
                method="deterministic_solver",
                reason="A different verifier changed this record.",
            ).model_dump(mode="json")
        },
    )
    with pytest.raises(CourseConflictError, match="snapshot changed"):
        await CourseService.set_exercise_verification(
            course_id="course:one",
            version=version,
            chapter=chapter,
            exercise=stored,
            verification=verification,
        )


@pytest.mark.asyncio
async def test_verification_transaction_rolls_back_when_current_version_pointer_changes(
    verification_database,
) -> None:
    repository = verification_database
    version = CourseVersion(
        **(await repository.repo_query("SELECT * FROM course_version:current;"))[0]
    )
    chapter = Chapter(
        **(await repository.repo_query("SELECT * FROM chapter:current;"))[0]
    )
    exercise = CourseExercise(
        **(await repository.repo_query("SELECT * FROM course_exercise:motion;"))[0]
    )
    verification = ExerciseVerification(
        level="L3",
        method="human_review",
        reason="Teacher checked the displayed answer.",
        verified_at=NOW,
    )
    await repository.repo_query(
        "UPDATE course:one SET outline_version_id = NONE;"
    )

    with pytest.raises(CourseConflictError, match="snapshot changed"):
        await CourseService.set_exercise_verification(
            course_id="course:one",
            version=version,
            chapter=chapter,
            exercise=exercise,
            verification=verification,
        )

    stored = CourseExercise(
        **(await repository.repo_query("SELECT * FROM course_exercise:motion;"))[0]
    )
    assert stored.verification.level == "L1"
