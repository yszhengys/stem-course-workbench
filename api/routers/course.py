"""Course module REST API (PDR-003).

Thin router: request validation lives in api.models, state rules live in
open_notebook/course/state_machine.py, persistence in the course domain
models. Generation/ingestion endpoints arrive in later milestones; this
surface covers CRUD and every state transition.
"""

from fastapi import APIRouter, HTTPException

from api.models import (
    AttemptCreate,
    AttemptStatusUpdate,
    ChapterCreate,
    ChapterUpdate,
    CourseCreate,
    CourseNoteCreate,
    CourseOutlineUpdate,
    CourseStatusUpdate,
    CourseUpdate,
    CourseVersionCreate,
    CourseVersionStatusUpdate,
    LabCreate,
    ProgressUpdate,
)
from open_notebook.course import state_machine as sm
from open_notebook.course.models import (
    Attempt,
    Chapter,
    Course,
    CourseNote,
    CourseVersion,
    Lab,
    Progress,
)
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.base import ObjectModel
from open_notebook.exceptions import InvalidInputError, NotFoundError, OpenNotebookError

router = APIRouter()

LAB_TYPES = {"canvas_plot", "geometry", "algebra_check", "data_fit", "mechanics_sim"}


def _body(model: ObjectModel) -> dict:
    """JSON-safe model payload (record ids and dates as strings)."""
    data = model.model_dump(mode="json")
    data["id"] = str(model.id)
    return data


# --- courses -----------------------------------------------------------------


@router.post("/courses")
async def create_course(request: CourseCreate):
    try:
        course = Course(
            title=request.title,
            subject=request.subject,
            description=request.description,
            config=request.config,
        )
        await course.save()
        return _body(course)
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating course: {e}")


@router.get("/courses")
async def list_courses():
    try:
        return [_body(c) for c in await Course.get_all(order_by="created desc")]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing courses: {e}")


@router.get("/courses/{course_id}")
async def get_course(course_id: str):
    try:
        return _body(await Course.get(course_id))
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching course: {e}")


@router.patch("/courses/{course_id}")
async def update_course(course_id: str, request: CourseUpdate):
    try:
        course: Course = await Course.get(course_id)
        if request.title is not None:
            course.title = request.title
        if request.subject is not None:
            course.subject = request.subject
        if request.description is not None:
            course.description = request.description
        if request.config is not None:
            course.config = request.config
        await course.save()
        return _body(course)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating course: {e}")


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str):
    try:
        course: Course = await Course.get(course_id)
        await course.delete()
        return {"message": "Course deleted"}
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting course: {e}")


@router.put("/courses/{course_id}/outline")
async def approve_outline(course_id: str, request: CourseOutlineUpdate):
    """Store the approved outline and move draft -> outline_approved.

    The exact-match 确认大纲 approval gate (PDR-003, decision 5) compares the
    *provided approval text* against the stored approved text via
    state_machine.approval_matches(); this endpoint records the approved
    outline payload and performs the transition.
    """
    try:
        course: Course = await Course.get(course_id)
        sm.validate_outline_approval_payload(request.outline)
        course.outline = request.outline
        await course.transition_to(sm.CourseStatus.OUTLINE_APPROVED)
        return _body(course)
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error approving outline: {e}")


@router.post("/courses/{course_id}/status")
async def transition_course(course_id: str, request: CourseStatusUpdate):
    try:
        course: Course = await Course.get(course_id)
        await course.transition_to(request.status)
        return _body(course)
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error transitioning course: {e}")


# --- versions ----------------------------------------------------------------


@router.post("/courses/{course_id}/versions")
async def create_version(course_id: str, request: CourseVersionCreate):
    """Snapshot a new (draft) version. Published versions are never edited —
    regeneration always creates a new version (PDR-003, decision 3)."""
    try:
        await Course.get(course_id)
        versions = await Course.versions(course_id)
        next_no = max((v.version_no for v in versions), default=0) + 1
        version = CourseVersion(
            course=course_id,
            version_no=next_no,
            outline_hash=request.outline_hash,
        )
        await version.save()
        return _body(version)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating version: {e}")


@router.get("/courses/{course_id}/versions")
async def list_versions(course_id: str):
    try:
        await Course.get(course_id)
        return [_body(v) for v in await Course.versions(course_id)]
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing versions: {e}")


@router.post("/versions/{version_id}/status")
async def transition_version(version_id: str, request: CourseVersionStatusUpdate):
    try:
        version: CourseVersion = await CourseVersion.get(version_id)
        await version.transition_to(request.status)
        return _body(version)
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course version not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error transitioning version: {e}"
        )


# --- chapters ----------------------------------------------------------------


@router.post("/versions/{version_id}/chapters")
async def create_chapter(version_id: str, request: ChapterCreate):
    try:
        await CourseVersion.get(version_id)
        chapter = Chapter(
            course_version=version_id,
            chapter_no=request.chapter_no,
            title=request.title,
        )
        await chapter.save()
        return _body(chapter)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course version not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating chapter: {e}")


@router.get("/versions/{version_id}/chapters")
async def list_chapters(version_id: str):
    try:
        await CourseVersion.get(version_id)
        return [_body(c) for c in await CourseVersion.chapters(version_id)]
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course version not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing chapters: {e}")


@router.patch("/chapters/{chapter_id}")
async def update_chapter(chapter_id: str, request: ChapterUpdate):
    try:
        chapter: Chapter = await Chapter.get(chapter_id)
        if request.title is not None:
            chapter.title = request.title
        if request.content is not None:
            chapter.content = request.content
        if request.citations is not None:
            chapter.citations = request.citations
        if request.review_status is not None:
            await chapter.transition_review(request.review_status)
        if request.validation_status is not None:
            await chapter.transition_validation(request.validation_status)
        await chapter.save()
        return _body(chapter)
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Chapter not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating chapter: {e}")


# --- labs --------------------------------------------------------------------


@router.post("/versions/{version_id}/labs")
async def create_lab(version_id: str, request: LabCreate):
    try:
        if request.lab_type not in LAB_TYPES:
            raise InvalidInputError(
                f"Unknown lab_type: {request.lab_type!r}. "
                f"Valid types: {sorted(LAB_TYPES)}"
            )
        await CourseVersion.get(version_id)
        lab = Lab(
            course_version=version_id,
            chapter=request.chapter,
            lab_type=request.lab_type,
            prompt=request.prompt,
            payload=request.payload,
            answer=request.answer,
        )
        await lab.save()
        return _body(lab)
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course version not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating lab: {e}")


@router.get("/versions/{version_id}/labs")
async def list_labs(version_id: str):
    try:
        await CourseVersion.get(version_id)
        return [_body(lab) for lab in await CourseVersion.labs(version_id)]
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course version not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing labs: {e}")


# --- attempts ----------------------------------------------------------------


@router.post("/labs/{lab_id}/attempts")
async def create_attempt(lab_id: str, request: AttemptCreate):
    try:
        lab: Lab = await Lab.get(lab_id)
        attempt = Attempt(lab=lab.id, answers=request.answers)
        await attempt.save()
        return _body(attempt)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Lab not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating attempt: {e}")


@router.get("/labs/{lab_id}/attempts")
async def list_attempts(lab_id: str):
    try:
        await Lab.get(lab_id)
        return [_body(a) for a in await Lab.attempts(lab_id)]
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Lab not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing attempts: {e}")


@router.post("/attempts/{attempt_id}/status")
async def transition_attempt(attempt_id: str, request: AttemptStatusUpdate):
    try:
        attempt: Attempt = await Attempt.get(attempt_id)
        await attempt.transition_to(request.status)
        return _body(attempt)
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Attempt not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error transitioning attempt: {e}"
        )


# --- progress ----------------------------------------------------------------


@router.get("/courses/{course_id}/progress")
async def list_progress(course_id: str):
    try:
        await Course.get(course_id)
        return [_body(p) for p in await Progress.list_by_course(course_id)]
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing progress: {e}")


@router.put("/courses/{course_id}/progress")
async def upsert_progress(course_id: str, request: ProgressUpdate):
    """Create or update the progress row for a chapter."""
    try:
        await Course.get(course_id)
        if request.chapter is not None:
            await Chapter.get(request.chapter)
        result = await repo_query(
            "SELECT * FROM progress WHERE course = $course_id AND chapter = $chapter_id",
            {
                "course_id": ensure_record_id(course_id),
                "chapter_id": (
                    ensure_record_id(request.chapter) if request.chapter else None
                ),
            },
        )
        progress = (
            Progress(**result[0])
            if result
            else Progress(course=course_id, chapter=request.chapter)
        )
        progress.status = sm.transition("progress", progress.status, request.status)
        await progress.save()
        return _body(progress)
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating progress: {e}")


# --- notes -------------------------------------------------------------------


@router.get("/courses/{course_id}/notes")
async def list_notes(course_id: str):
    try:
        await Course.get(course_id)
        return [_body(n) for n in await CourseNote.list_by_course(course_id)]
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing notes: {e}")


@router.post("/courses/{course_id}/notes")
async def create_note(course_id: str, request: CourseNoteCreate):
    try:
        await Course.get(course_id)
        note = CourseNote(
            course=course_id,
            chapter=request.chapter,
            content=request.content,
        )
        await note.save()
        return _body(note)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Course not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating note: {e}")


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str):
    try:
        note: CourseNote = await CourseNote.get(note_id)
        await note.delete()
        return {"message": "Note deleted"}
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Note not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting note: {e}")
