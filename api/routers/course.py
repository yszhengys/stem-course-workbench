"""Thin Course HTTP adapter; workflow and persistence live in CourseService."""

from __future__ import annotations

from typing import Any, Awaitable, TypeVar

from fastapi import APIRouter, HTTPException, status

from api.course_command_service import CourseCommandService, CourseJobSubmission
from api.course_service import (
    CourseApprovalError,
    CourseConflictError,
    CourseService,
)
from api.models import (
    AttemptCreate,
    AttemptStatusUpdate,
    ChapterCreate,
    ChapterPublish,
    ChapterUpdate,
    CourseChapterGenerateRequest,
    CourseChapterReviewRequest,
    CourseCreate,
    CourseEvidenceBuildRequest,
    CourseFindingUpdate,
    CourseJobResponse,
    CourseNoteCreate,
    CourseOutlineApproval,
    CourseOutlineGenerateRequest,
    CourseRetrievalRequest,
    CourseSourceAssociation,
    CourseUpdate,
    CourseVersionCreate,
    LabCreate,
    ProgressUpdate,
)
from open_notebook.domain.base import ObjectModel
from open_notebook.exceptions import InvalidInputError, NotFoundError, OpenNotebookError

router = APIRouter()
course_commands = CourseCommandService()
ResultT = TypeVar("ResultT")
LAB_TYPES = {
    "function_plot",
    "parametric_curve",
    "vector_field",
    "geometry",
    "kinematics",
}


def _body(model: ObjectModel) -> dict[str, Any]:
    data = model.model_dump(mode="json")
    data["id"] = str(model.id) if model.id is not None else None
    return data


def _job(submission: CourseJobSubmission) -> CourseJobResponse:
    return CourseJobResponse(
        command_id=submission.command_id,
        run_id=submission.run_id,
        status=submission.status,
    )


async def _call(operation: Awaitable[ResultT]) -> ResultT:
    try:
        return await operation
    except CourseApprovalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CourseConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Course resource not found"
        ) from exc
    except OpenNotebookError as exc:
        raise OpenNotebookError("Course operation failed") from exc
    except Exception as exc:
        raise OpenNotebookError("Course operation failed") from exc


@router.post("/courses", status_code=status.HTTP_201_CREATED)
async def create_course(request: CourseCreate):
    course = await _call(CourseService.create_course(**request.model_dump()))
    return _body(course)


@router.get("/courses")
async def list_courses():
    return [_body(course) for course in await _call(CourseService.list_courses())]


@router.get("/courses/model-options")
async def get_course_model_options():
    return await _call(CourseService.get_model_options())


@router.get("/courses/{course_id}")
async def get_course(course_id: str):
    return _body(await _call(CourseService.get_course(course_id)))


@router.patch("/courses/{course_id}")
async def update_course(course_id: str, request: CourseUpdate):
    course = await _call(
        CourseService.update_course(course_id, request.model_dump(exclude_unset=True))
    )
    return _body(course)


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str):
    await _call(CourseService.delete_course(course_id))
    return {"message": "Course deleted"}


@router.post("/courses/{course_id}/sources")
async def associate_source(course_id: str, request: CourseSourceAssociation):
    course = await _call(
        CourseService.associate_source(course_id, request.source_id, request.role)
    )
    return _body(course)


@router.get("/courses/{course_id}/sources/eligible")
async def list_eligible_sources(course_id: str):
    return await _call(course_commands.eligible_sources(course_id))


@router.post(
    "/courses/{course_id}/evidence/build",
    response_model=CourseJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def build_evidence(course_id: str, request: CourseEvidenceBuildRequest):
    submission = await _call(
        course_commands.submit_evidence(
            course_id=course_id,
            source_id=request.source_id,
            role=request.role,
            force=request.force,
        )
    )
    return _job(submission)


@router.get("/courses/{course_id}/evidence/anchors")
async def list_evidence_anchors(course_id: str):
    return [
        _body(anchor)
        for anchor in await _call(course_commands.list_anchors(course_id))
    ]


@router.post(
    "/courses/{course_id}/outline/generate",
    response_model=CourseJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_outline(course_id: str, request: CourseOutlineGenerateRequest):
    submission = await _call(
        course_commands.submit_outline(
            course_id=course_id,
            anchor_ids=request.anchor_ids,
            available_lab_keys=request.available_lab_keys,
            prompt_version=request.prompt_version,
            model=request.model,
            force=request.force,
        )
    )
    return _job(submission)


@router.get("/courses/{course_id}/outline/current")
async def get_current_outline(course_id: str):
    return _body(await _call(course_commands.current_outline(course_id)))


@router.post(
    "/courses/{course_id}/chapters/{chapter_key}/generate",
    response_model=CourseJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_chapter(
    course_id: str, chapter_key: str, request: CourseChapterGenerateRequest
):
    submission = await _call(
        course_commands.submit_chapter(
            course_id=course_id,
            chapter_key=chapter_key,
            anchor_ids=request.anchor_ids,
            prompt_version=request.prompt_version,
            model=request.model,
            force=request.force,
        )
    )
    return _job(submission)


@router.post(
    "/courses/{course_id}/chapters/{chapter_key}/review",
    response_model=CourseJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def review_chapter(
    course_id: str, chapter_key: str, request: CourseChapterReviewRequest
):
    submission = await _call(
        course_commands.submit_review(
            course_id=course_id,
            chapter_key=chapter_key,
            anchor_ids=request.anchor_ids,
            prompt_version=request.prompt_version,
            model=request.model,
            force=request.force,
        )
    )
    return _job(submission)


@router.get("/courses/{course_id}/chapters/{chapter_key}")
async def get_current_chapter(course_id: str, chapter_key: str):
    return _body(
        await _call(course_commands.current_chapter(course_id, chapter_key))
    )


@router.get("/courses/{course_id}/runs/{run_id}")
async def get_generation_run(course_id: str, run_id: str):
    return _body(await _call(course_commands.get_run(course_id, run_id)))


@router.get("/courses/{course_id}/findings")
async def list_validation_findings(
    course_id: str, chapter_key: str | None = None
):
    return [
        _body(finding)
        for finding in await _call(
            course_commands.list_findings(course_id, chapter_key)
        )
    ]


@router.patch("/courses/{course_id}/findings/{finding_id}")
async def update_validation_finding(
    course_id: str, finding_id: str, request: CourseFindingUpdate
):
    finding = await _call(
        course_commands.update_finding(
            course_id=course_id,
            finding_id=finding_id,
            status=request.status,
            resolution_reason=request.resolution_reason,
        )
    )
    return _body(finding)


@router.post("/courses/{course_id}/retrieval/context")
async def retrieval_context(course_id: str, request: CourseRetrievalRequest):
    return await _call(
        course_commands.retrieval_context(course_id, request.anchor_ids)
    )


@router.post("/courses/{course_id}/outline/approve")
async def approve_outline(course_id: str, request: CourseOutlineApproval):
    version = await _call(
        CourseService.approve_outline(
            course_id, request.version_id, request.confirmation
        )
    )
    return _body(version)


@router.post("/courses/{course_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(course_id: str, request: CourseVersionCreate):
    version = await _call(
        CourseService.create_version(course_id, request.model_dump(exclude_unset=True))
    )
    return _body(version)


@router.get("/courses/{course_id}/versions")
async def list_versions(course_id: str):
    return [
        _body(version)
        for version in await _call(CourseService.list_versions(course_id))
    ]


@router.post("/versions/{version_id}/publish")
async def publish_version(version_id: str):
    return _body(await _call(CourseService.publish_version(version_id)))


@router.post("/versions/{version_id}/chapters", status_code=status.HTTP_201_CREATED)
async def create_chapter(version_id: str, request: ChapterCreate):
    chapter = await _call(
        CourseService.create_chapter(version_id, request.model_dump(exclude_unset=True))
    )
    return _body(chapter)


@router.get("/versions/{version_id}/chapters")
async def list_chapters(version_id: str):
    return [
        _body(chapter)
        for chapter in await _call(CourseService.list_chapters(version_id))
    ]


@router.patch("/versions/{version_id}/chapters/{chapter_id}")
async def update_chapter(
    version_id: str, chapter_id: str, request: ChapterUpdate
):
    chapter = await _call(
        CourseService.update_chapter(
            version_id, chapter_id, request.model_dump(exclude_unset=True)
        )
    )
    return _body(chapter)


@router.post("/versions/{version_id}/chapters/{chapter_id}/publish")
async def publish_chapter(
    version_id: str, chapter_id: str, request: ChapterPublish
):
    chapter = await _call(
        CourseService.publish_chapter(request.course_id, version_id, chapter_id)
    )
    return _body(chapter)


@router.post("/versions/{version_id}/labs", status_code=status.HTTP_201_CREATED)
async def create_lab(version_id: str, request: LabCreate):
    if request.lab_type not in LAB_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported lab_type")
    lab = await _call(
        CourseService.create_lab(
            version_id, request.model_dump(exclude_unset=True, by_alias=True)
        )
    )
    return _body(lab)


@router.get("/versions/{version_id}/labs")
async def list_labs(version_id: str):
    return [_body(lab) for lab in await _call(CourseService.list_labs(version_id))]


@router.post("/labs/{lab_id}/attempts", status_code=status.HTTP_201_CREATED)
async def create_attempt(lab_id: str, request: AttemptCreate):
    attempt = await _call(
        CourseService.create_attempt(lab_id, request.model_dump(exclude_unset=True))
    )
    return _body(attempt)


@router.get("/labs/{lab_id}/attempts")
async def list_attempts(lab_id: str):
    return [
        _body(attempt)
        for attempt in await _call(CourseService.list_attempts(lab_id))
    ]


@router.post("/attempts/{attempt_id}/status")
async def transition_attempt(attempt_id: str, request: AttemptStatusUpdate):
    return _body(
        await _call(CourseService.transition_attempt(attempt_id, request.status))
    )


@router.get("/courses/{course_id}/progress")
async def list_progress(course_id: str):
    return [
        _body(progress)
        for progress in await _call(CourseService.list_progress(course_id))
    ]


@router.put("/courses/{course_id}/progress")
async def upsert_progress(course_id: str, request: ProgressUpdate):
    progress = await _call(
        CourseService.upsert_progress(
            course_id, request.model_dump(exclude_unset=True)
        )
    )
    return _body(progress)


@router.get("/courses/{course_id}/notes")
async def list_notes(course_id: str):
    return [_body(note) for note in await _call(CourseService.list_notes(course_id))]


@router.post("/courses/{course_id}/notes", status_code=status.HTTP_201_CREATED)
async def create_note(course_id: str, request: CourseNoteCreate):
    note = await _call(
        CourseService.create_note(course_id, request.model_dump(exclude_unset=True))
    )
    return _body(note)


@router.delete("/courses/{course_id}/notes/{note_id}")
async def delete_note(course_id: str, note_id: str):
    await _call(CourseService.delete_note(course_id, note_id))
    return {"message": "Note deleted"}
