import pytest

from open_notebook.course.models import CourseGenerationRun
from open_notebook.course.workflow_service import CourseWorkflowService
from tests.course.test_exercise_workflow_persistence import (
    REVIEW_MODEL,
    WorkflowHarness,
)
from tests.course.test_exercise_workflow_persistence import (
    workflow_harness as _workflow_harness,
)

workflow_harness = _workflow_harness


@pytest.mark.asyncio
async def test_succeeded_review_child_replays_without_another_model_call(
    workflow_harness: WorkflowHarness,
) -> None:
    await workflow_harness.create_parent_run(
        run_id="course_generation_run:parent", command_id="command:one"
    )
    exercises = await workflow_harness.generate(
        run_id="course_generation_run:parent", command_id="command:one"
    )
    core = next(item.blueprint for item in exercises if item.is_core)
    parent = await CourseGenerationRun.get("course_generation_run:parent")
    chapter = await CourseWorkflowService.resolve_current_chapter(
        course_id="course:one",
        version_id="course_version:one",
        chapter_key="linear-equations",
    )
    anchors, source_hashes, _ = await workflow_harness.service.workflow.grounded_inputs(
        course=await workflow_harness.service.load_course("course:one"),
        anchor_ids=[workflow_harness.anchor_id],
    )
    before = len(workflow_harness.adapter.calls)

    findings, child_id = await workflow_harness.service.review_transfer(
        parent_run=parent,
        course_id="course:one",
        version_id="course_version:one",
        chapter=chapter,
        core=core,
        selected_anchors=anchors,
        source_hashes=source_hashes,
        model=REVIEW_MODEL,
        prompt_version="v2",
    )

    assert findings == ()
    assert child_id in next(item for item in exercises if item.is_core).review_run_ids
    assert len(workflow_harness.adapter.calls) == before


@pytest.mark.asyncio
async def test_tampered_or_terminal_review_child_fails_closed_without_retry(
    workflow_harness: WorkflowHarness,
) -> None:
    await workflow_harness.create_parent_run(
        run_id="course_generation_run:parent", command_id="command:one"
    )
    exercises = await workflow_harness.generate(
        run_id="course_generation_run:parent", command_id="command:one"
    )
    core_record = next(item for item in exercises if item.is_core)
    child_id = core_record.review_run_ids[0]
    await workflow_harness.repository.repo_query(
        "UPDATE $child SET output_hash = $hash;",
        {
            "child": workflow_harness.repository.ensure_record_id(child_id),
            "hash": "0" * 64,
        },
    )
    parent = await CourseGenerationRun.get("course_generation_run:parent")
    chapter = await CourseWorkflowService.resolve_current_chapter(
        course_id="course:one",
        version_id="course_version:one",
        chapter_key="linear-equations",
    )
    course = await workflow_harness.service.load_course("course:one")
    anchors, source_hashes, _ = await workflow_harness.service.workflow.grounded_inputs(
        course=course, anchor_ids=[workflow_harness.anchor_id]
    )
    before = len(workflow_harness.adapter.calls)

    with pytest.raises(ValueError, match="output hash"):
        await workflow_harness.service.review_transfer(
            parent_run=parent,
            course_id="course:one",
            version_id="course_version:one",
            chapter=chapter,
            core=core_record.blueprint,
            selected_anchors=anchors,
            source_hashes=source_hashes,
            model=REVIEW_MODEL,
            prompt_version="v2",
        )

    assert len(workflow_harness.adapter.calls) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_terminal_review_child_is_never_replaced_or_retried(
    workflow_harness: WorkflowHarness,
    status: str,
) -> None:
    await workflow_harness.create_parent_run(
        run_id="course_generation_run:parent", command_id="command:one"
    )
    exercises = await workflow_harness.generate(
        run_id="course_generation_run:parent", command_id="command:one"
    )
    core_record = next(item for item in exercises if item.is_core)
    child_id = core_record.review_run_ids[0]
    await workflow_harness.repository.repo_query(
        "UPDATE $child SET status = $status, output_hash = NONE;",
        {
            "child": workflow_harness.repository.ensure_record_id(child_id),
            "status": status,
        },
    )
    parent = await CourseGenerationRun.get("course_generation_run:parent")
    chapter = await CourseWorkflowService.resolve_current_chapter(
        course_id="course:one",
        version_id="course_version:one",
        chapter_key="linear-equations",
    )
    course = await workflow_harness.service.load_course("course:one")
    anchors, source_hashes, _ = await workflow_harness.service.workflow.grounded_inputs(
        course=course, anchor_ids=[workflow_harness.anchor_id]
    )
    before = len(workflow_harness.adapter.calls)

    with pytest.raises(ValueError, match="terminal"):
        await workflow_harness.service.review_transfer(
            parent_run=parent,
            course_id="course:one",
            version_id="course_version:one",
            chapter=chapter,
            core=core_record.blueprint,
            selected_anchors=anchors,
            source_hashes=source_hashes,
            model=REVIEW_MODEL,
            prompt_version="v2",
        )

    assert len(workflow_harness.adapter.calls) == before
    matching = await workflow_harness.repository.repo_query(
        "SELECT id FROM course_generation_run "
        "WHERE stage = 'exercise_bank_review';"
    )
    assert [str(row["id"]) for row in matching] == [child_id]
