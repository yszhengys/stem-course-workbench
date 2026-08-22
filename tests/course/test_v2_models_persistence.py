from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from surrealdb import AsyncSurreal

from open_notebook.course.contracts import ModelSelection
from open_notebook.course.v2_contracts import (
    BundleFileManifest,
    BundleRecordCount,
    CourseBundleManifest,
    DifficultyVector,
    ExerciseBlueprint,
    PositionPayload,
    ReplaceFormulaOperation,
    SymbolicGraderSpec,
    TransferTaskSpec,
)
from open_notebook.course.v2_models import (
    CourseConceptMastery,
    CourseDraftRevision,
    CourseExercise,
    CourseExport,
    CourseLearningEvent,
    CourseTutorSession,
    CourseTutorTurn,
)


def migration_sql(version: str) -> str:
    return Path(
        f"open_notebook/database/migrations/{version}.surrealql"
    ).read_text()


def exercise_blueprint() -> ExerciseBlueprint:
    difficulty = DifficultyVector(
        concept_count=1,
        reasoning_steps=3,
        symbolic_depth=2,
        representation_shifts=1,
        proof_burden=0,
        physics_constraints=0,
    )
    grader = SymbolicGraderSpec(
        kind="symbolic", expected_expression="1", allowed_symbols=[]
    )
    return ExerciseBlueprint(
        key="limits-core",
        chapter_key="limits",
        prompt="Evaluate the grounded limit.",
        concept_keys=["limit"],
        exercise_type="worked_source",
        answer_type="symbolic",
        source_anchor_ids=["anchor:one"],
        difficulty=difficulty,
        grader=grader,
        is_core=True,
        is_gating=True,
        is_source_level=True,
        transfer_task=TransferTaskSpec(
            key="limits-inverse",
            prompt="Construct a function with the requested limiting behavior.",
            invariant_concept_keys=["limit"],
            dimensions=["inverse_or_constructive"],
            answer_type="symbolic",
            difficulty=difficulty,
            grader=grader,
            anchor_ids=["anchor:one"],
        ),
    )


@pytest.mark.asyncio
async def test_all_v2_models_persist_with_real_surreal_schema(monkeypatch) -> None:
    import open_notebook.database.repository as repository

    database = AsyncSurreal("mem://")
    await database.use("course_v2_models", "course_v2_models")

    @asynccontextmanager
    async def memory_connection():
        yield database

    monkeypatch.setattr(repository, "db_connection", memory_connection)
    await database.query(
        "DEFINE TABLE notebook SCHEMALESS; DEFINE TABLE source SCHEMALESS;"
    )
    for version in ("24", "25", "26"):
        await database.query(migration_sql(version))
    await database.query(
        """
        CREATE notebook:one SET name = 'Notebook';
        CREATE course:one SET
            title = 'Calculus', notebook = notebook:one,
            source_ids = [], primary_source_ids = [], supplement_source_ids = [];
        CREATE course_version:one SET course = course:one, version_no = 1;
        CREATE chapter:one SET
            course_version = course_version:one, chapter_no = 1,
            chapter_key = 'limits', title = 'Limits';
        """
    )
    blueprint = exercise_blueprint()
    exercise = CourseExercise(
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key=blueprint.chapter_key,
        exercise_key=blueprint.key,
        blueprint=blueprint,
        source_anchor_ids=blueprint.source_anchor_ids,
        difficulty=blueprint.difficulty,
        grader=blueprint.grader,
        is_core=True,
        is_gating=True,
        is_source_level=True,
    )
    event = CourseLearningEvent(
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="limits",
        concept_key="limit",
        event_key="event-1",
        kind="chapter_opened",
        payload=PositionPayload(block_key="section-1"),
        occurred_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
    )
    mastery = CourseConceptMastery(
        course="course:one",
        course_version="course_version:one",
        chapter_key="limits",
        concept_key="limit",
        snapshot_hash="a" * 64,
    )
    session = CourseTutorSession(
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="limits",
        model_selection=ModelSelection(
            adapter="codex_cli", model="gpt-5.6-sol", reasoning_effort="max"
        ),
    )
    await exercise.save()
    await event.save()
    await mastery.save()
    await session.save()
    assert session.id is not None
    turn = CourseTutorTurn(
        course="course:one",
        course_version="course_version:one",
        session=str(session.id),
        chapter_key="limits",
        turn_no=1,
        role="assistant",
        content="The cited source defines the limit.",
        anchor_ids=["anchor:one"],
    )
    revision = CourseDraftRevision(
        course="course:one",
        course_version="course_version:one",
        chapter="chapter:one",
        chapter_key="limits",
        revision_no=1,
        base_artifact_hash="b" * 64,
        artifact_hash="c" * 64,
        operation=ReplaceFormulaOperation(
            kind="replace_formula",
            block_key="formula-1",
            latex="f(x) = x",
            anchor_ids=["anchor:one"],
        ),
        invalidated_checks=["formula", "numeric"],
    )
    export = CourseExport(
        course="course:one",
        status="succeeded",
        bundle_path="notebook_data/course_exports/calculus.stemcourse",
        manifest=CourseBundleManifest(
            schema_version=1,
            app_version="2.0.0-dev",
            course_title="Calculus",
            exported_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            record_counts=[BundleRecordCount(record_type="course", count=1)],
            files=[
                BundleFileManifest(
                    path="records/course.json", size_bytes=20, sha256="d" * 64
                )
            ],
        ),
    )
    await turn.save()
    await revision.save()
    await export.save()

    for model in (exercise, event, mastery, session, turn, revision, export):
        assert model.id is not None
        rows = cast(
            list[dict[str, object]], await database.query(f"SELECT id FROM {model.id};")
        )
        assert len(rows) == 1
    await database.close()
