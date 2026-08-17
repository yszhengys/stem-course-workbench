"""Thin Course HTTP adapter; workflow and persistence live in CourseService."""

from __future__ import annotations

from typing import Any, Awaitable, TypeVar

from fastapi import APIRouter, HTTPException, status

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
    CourseCreate,
    CourseNoteCreate,
    CourseOutlineApproval,
    CourseSourceAssociation,
    CourseUpdate,
    CourseVersionCreate,
    LabCreate,
    ProgressUpdate,
)
from open_notebook.domain.base import ObjectModel
from open_notebook.exceptions import InvalidInputError, NotFoundError, OpenNotebookError

router = APIRouter()
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
