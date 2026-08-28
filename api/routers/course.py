"""Thin Course HTTP adapter; workflow and persistence live in CourseService."""

from __future__ import annotations

from pathlib import PurePath
from typing import Any, Awaitable, TypeVar

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response

from api.course_command_service import CourseCommandService, CourseJobSubmission
from api.course_service import (
    CourseApprovalError,
    CourseConflictError,
    CourseService,
)
from api.course_v2_service import course_v2_service
from api.models import (
    AttemptCreate,
    AttemptStatusUpdate,
    ChapterCreate,
    ChapterPublish,
    ChapterUpdate,
    CourseAcademicVerificationRequest,
    CourseActivityEventRequest,
    CourseBibliographicSourceResponse,
    CourseBibliographyUpdateRequest,
    CourseBundleImportResponse,
    CourseChapterAttemptCreate,
    CourseChapterGenerateRequest,
    CourseChapterReviewRequest,
    CourseCreate,
    CourseDraftOperationRequest,
    CourseDraftResponse,
    CourseDraftValidateRequest,
    CourseDraftValidationResponse,
    CourseEvidenceBuildRequest,
    CourseExerciseBankGenerateRequest,
    CourseExerciseBuildStatusResponse,
    CourseExerciseGradeRequest,
    CourseExerciseGradeResponse,
    CourseExerciseHintRequest,
    CourseExerciseHintResponse,
    CourseExerciseResponse,
    CourseExerciseRevealRequest,
    CourseExerciseRevealResponse,
    CourseExerciseVerificationRequest,
    CourseExportCreateRequest,
    CourseExportResponse,
    CourseFindingUpdate,
    CourseJobResponse,
    CourseLabApprovalRequest,
    CourseLabApprovalResponse,
    CourseLearnerChapterResponse,
    CourseLearnerNoteCreateRequest,
    CourseLearnerNoteResponse,
    CourseLearnerNotesResponse,
    CourseLearnerSourcesResponse,
    CourseLearningEventResponse,
    CourseLearningOverviewResponse,
    CourseLearningUpgradeRequest,
    CourseLearningUpgradeResponse,
    CourseNoteCreate,
    CourseNoteReattach,
    CourseOutlineApproval,
    CourseOutlineGenerateRequest,
    CourseRetrievalRequest,
    CourseSourceAssociation,
    CourseTransferGradeRequest,
    CourseTutorMessageRequest,
    CourseTutorMessageResponse,
    CourseTutorSessionCreateRequest,
    CourseTutorSessionResponse,
    CourseUpdate,
    CourseVersionCreate,
    ExerciseVerificationResponse,
    LabCreate,
    ProgressUpdate,
)
from open_notebook.course.v2_contracts import (
    AcademicArtifactKind,
    DraftTargetKey,
    ReviewQueueItem,
    StableKey,
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
COURSE_BUNDLE_MAX_UPLOAD_BYTES = 256 * 1024 * 1024


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


@router.post(
    "/courses/{course_id}/versions/prepare-learning-upgrade",
    response_model=CourseLearningUpgradeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_course_learning_upgrade(
    course_id: str,
    request: CourseLearningUpgradeRequest,
):
    return await _call(
        course_v2_service.prepare_learning_upgrade(course_id, request)
    )


@router.post(
    "/courses/imports",
    response_model=CourseBundleImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_course_bundle(bundle: UploadFile = File(...)):
    filename = bundle.filename or ""
    if PurePath(filename).suffix.lower() != ".stemcourse":
        raise HTTPException(
            status_code=422,
            detail="Course bundle filename must end in .stemcourse.",
        )
    payload = await bundle.read(COURSE_BUNDLE_MAX_UPLOAD_BYTES + 1)
    await bundle.close()
    if len(payload) > COURSE_BUNDLE_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Course bundle is too large.")
    return await _call(course_v2_service.import_course_bundle(payload))


@router.post(
    "/courses/{course_id}/exports",
    response_model=CourseExportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_course_export(
    course_id: str,
    request: CourseExportCreateRequest,
):
    return await _call(
        course_v2_service.create_course_export(
            course_id,
            include_originals=request.include_originals,
        )
    )


@router.get(
    "/courses/{course_id}/exports/{export_id}",
    response_model=CourseExportResponse,
)
async def get_course_export(course_id: str, export_id: str):
    return await _call(
        course_v2_service.get_course_export(course_id, export_id)
    )


@router.get("/courses/{course_id}/exports/{export_id}/download")
async def download_course_export(course_id: str, export_id: str):
    bundle_path = await _call(
        course_v2_service.get_course_export_path(course_id, export_id)
    )
    return FileResponse(
        path=bundle_path,
        filename=f"{export_id.partition(':')[2]}.stemcourse",
        media_type="application/zip",
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/courses")
async def list_courses():
    return [_body(course) for course in await _call(CourseService.list_courses())]


@router.get("/courses/model-options")
async def get_course_model_options():
    return await _call(CourseService.get_model_options())


@router.get("/courses/{course_id}")
async def get_course(course_id: str):
    return _body(await _call(CourseService.get_course(course_id)))


@router.get(
    "/courses/{course_id}/learning/overview",
    response_model=CourseLearningOverviewResponse,
)
async def get_learning_overview(course_id: str):
    return await _call(course_v2_service.get_learning_overview(course_id))


@router.get(
    "/courses/{course_id}/learning/review-queue",
    response_model=list[ReviewQueueItem],
)
async def get_learning_review_queue(course_id: str):
    return await _call(course_v2_service.get_review_queue(course_id))


@router.post(
    "/courses/{course_id}/learning/events",
    response_model=CourseLearningEventResponse,
)
async def append_learning_event(
    course_id: str,
    request: CourseActivityEventRequest,
):
    return await _call(
        course_v2_service.append_activity_event(course_id, request)
    )


@router.get(
    "/courses/{course_id}/learning/chapters/{chapter_key}",
    response_model=CourseLearnerChapterResponse,
)
async def get_learning_chapter(course_id: str, chapter_key: StableKey):
    return await _call(
        course_v2_service.get_learning_chapter(course_id, chapter_key)
    )


@router.get(
    "/courses/{course_id}/learning/chapters/{chapter_key}/sources",
    response_model=CourseLearnerSourcesResponse,
)
async def list_learning_sources(course_id: str, chapter_key: StableKey):
    return await _call(
        course_v2_service.list_learning_sources(course_id, chapter_key)
    )


@router.get(
    "/courses/{course_id}/learning/chapters/{chapter_key}/notes",
    response_model=CourseLearnerNotesResponse,
)
async def list_learning_notes(course_id: str, chapter_key: StableKey):
    return await _call(
        course_v2_service.list_learning_notes(course_id, chapter_key)
    )


@router.post(
    "/courses/{course_id}/learning/chapters/{chapter_key}/notes",
    response_model=CourseLearnerNoteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_learning_note(
    course_id: str,
    chapter_key: StableKey,
    request: CourseLearnerNoteCreateRequest,
):
    return await _call(
        course_v2_service.create_learning_note(
            course_id, chapter_key, request
        )
    )


@router.post(
    "/courses/{course_id}/tutor/sessions",
    response_model=CourseTutorSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tutor_session(
    course_id: str,
    request: CourseTutorSessionCreateRequest,
):
    return await _call(
        course_v2_service.create_tutor_session(course_id, request)
    )


@router.get(
    "/courses/{course_id}/tutor/sessions",
    response_model=list[CourseTutorSessionResponse],
)
async def list_tutor_sessions(course_id: str):
    return await _call(course_v2_service.list_tutor_sessions(course_id))


@router.post(
    "/courses/{course_id}/tutor/sessions/{session_id}/messages",
    response_model=CourseTutorMessageResponse,
)
async def respond_to_tutor(
    course_id: str,
    session_id: str,
    request: CourseTutorMessageRequest,
):
    return await _call(
        course_v2_service.respond_to_tutor(
            course_id, session_id, request
        )
    )


@router.get(
    "/courses/{course_id}/chapters/{chapter_key}/draft",
    response_model=CourseDraftResponse,
)
async def get_chapter_draft(course_id: str, chapter_key: StableKey):
    return await _call(
        course_v2_service.get_chapter_draft(course_id, chapter_key)
    )


@router.patch(
    "/courses/{course_id}/chapters/{chapter_key}/draft",
    response_model=CourseDraftResponse,
)
async def apply_chapter_draft_operation(
    course_id: str,
    chapter_key: StableKey,
    request: CourseDraftOperationRequest,
):
    return await _call(
        course_v2_service.apply_chapter_draft_operation(
            course_id, chapter_key, request
        )
    )


@router.post(
    "/courses/{course_id}/chapters/{chapter_key}/artifacts/"
    "{target_kind}/{target_key}/verify",
    response_model=CourseDraftResponse,
)
async def verify_academic_artifact(
    course_id: str,
    chapter_key: StableKey,
    target_kind: AcademicArtifactKind,
    target_key: DraftTargetKey,
    request: CourseAcademicVerificationRequest,
):
    return await _call(
        course_v2_service.verify_academic_artifact(
            course_id,
            chapter_key,
            target_kind,
            target_key,
            request,
        )
    )


@router.post(
    "/courses/{course_id}/chapters/{chapter_key}/draft/validate",
    response_model=CourseDraftValidationResponse,
)
async def validate_chapter_draft(
    course_id: str,
    chapter_key: StableKey,
    request: CourseDraftValidateRequest,
):
    return await _call(
        course_v2_service.validate_chapter_draft(
            course_id, chapter_key, request
        )
    )


@router.get(
    "/courses/{course_id}/exercises",
    response_model=list[CourseExerciseResponse],
)
async def list_course_exercises(
    course_id: str,
    chapter_key: StableKey | None = None,
):
    return await _call(
        course_v2_service.list_exercises(course_id, chapter_key)
    )


@router.post(
    "/courses/{course_id}/chapters/{chapter_key}/exercises/{exercise_key}/verify",
    response_model=ExerciseVerificationResponse,
)
async def verify_course_exercise(
    course_id: str,
    chapter_key: StableKey,
    exercise_key: StableKey,
    request: CourseExerciseVerificationRequest,
):
    return await _call(
        course_v2_service.verify_exercise(
            course_id, chapter_key, exercise_key, request
        )
    )


@router.post(
    "/courses/{course_id}/exercises/{exercise_key}/grade",
    response_model=CourseExerciseGradeResponse,
)
async def grade_course_exercise(
    course_id: str,
    exercise_key: StableKey,
    request: CourseExerciseGradeRequest,
):
    return await _call(
        course_v2_service.grade_exercise(course_id, exercise_key, request)
    )


@router.post(
    "/courses/{course_id}/exercises/{exercise_key}/hints/next",
    response_model=CourseExerciseHintResponse,
)
async def get_next_course_exercise_hint(
    course_id: str,
    exercise_key: StableKey,
    request: CourseExerciseHintRequest,
):
    return await _call(
        course_v2_service.next_hint(course_id, exercise_key, request)
    )


@router.post(
    "/courses/{course_id}/exercises/{exercise_key}/reveal",
    response_model=CourseExerciseRevealResponse,
)
async def reveal_course_exercise_answer(
    course_id: str,
    exercise_key: StableKey,
    request: CourseExerciseRevealRequest,
):
    return await _call(
        course_v2_service.reveal_answer(course_id, exercise_key, request)
    )


@router.post(
    "/courses/{course_id}/exercises/{exercise_key}/transfer/grade",
    response_model=CourseExerciseGradeResponse,
)
async def grade_course_transfer(
    course_id: str,
    exercise_key: StableKey,
    request: CourseTransferGradeRequest,
):
    return await _call(
        course_v2_service.grade_transfer(course_id, exercise_key, request)
    )


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


@router.get(
    "/courses/{course_id}/bibliography",
    response_model=list[CourseBibliographicSourceResponse],
)
async def list_course_bibliography(course_id: str):
    return await _call(course_v2_service.list_bibliography(course_id))


@router.get(
    "/courses/{course_id}/bibliography/csl-json",
    response_model=list[dict[str, Any]],
)
async def get_course_bibliography_csl_json(course_id: str):
    return await _call(course_v2_service.csl_json(course_id))


@router.get(
    "/courses/{course_id}/sources/{source_id}/bibliography",
    response_model=CourseBibliographicSourceResponse,
)
async def get_course_source_bibliography(course_id: str, source_id: str):
    return await _call(
        course_v2_service.get_bibliography(course_id, source_id)
    )


@router.put(
    "/courses/{course_id}/sources/{source_id}/bibliography",
    response_model=CourseBibliographicSourceResponse,
)
async def put_course_source_bibliography(
    course_id: str,
    source_id: str,
    request: CourseBibliographyUpdateRequest,
):
    return await _call(
        course_v2_service.put_bibliography(course_id, source_id, request)
    )


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


@router.get("/courses/{course_id}/evidence/anchors/{anchor_id}/preview")
async def get_evidence_preview(course_id: str, anchor_id: str):
    asset = await _call(CourseService.get_evidence_preview(course_id, anchor_id))
    return Response(
        content=asset.content,
        media_type=asset.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="{asset.filename}"',
            "Content-Security-Policy": "default-src 'none'; style-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
            "X-Course-Preview-Mode": asset.mode,
        },
    )


@router.get("/courses/{course_id}/evidence/anchors/{anchor_id}/source")
async def get_evidence_source(
    course_id: str, anchor_id: str, download: bool = False
):
    asset = await _call(CourseService.get_evidence_source(course_id, anchor_id))
    media_type = (
        "application/pdf"
        if asset.kind == "pdf"
        else "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    return FileResponse(
        path=asset.path,
        filename=asset.filename,
        media_type=media_type,
        content_disposition_type="attachment" if download else "inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
            escalation_model=request.escalation_model,
            force=request.force,
        )
    )
    return _job(submission)


@router.post(
    "/courses/{course_id}/chapters/{chapter_key}/exercises/generate",
    response_model=CourseJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_chapter_exercises(
    course_id: str,
    chapter_key: str,
    request: CourseExerciseBankGenerateRequest,
):
    submission = await _call(
        course_commands.submit_exercise_bank(
            course_id=course_id,
            chapter_key=chapter_key,
            anchor_ids=request.anchor_ids,
            prompt_version=request.prompt_version,
            model=request.model,
            review_model=request.review_model,
            force=request.force,
        )
    )
    return _job(submission)


@router.get(
    "/courses/{course_id}/chapters/{chapter_key}/exercises/build-status",
    response_model=CourseExerciseBuildStatusResponse,
)
async def get_chapter_exercise_build_status(
    course_id: str, chapter_key: str
):
    return await _call(
        course_v2_service.exercise_build_status(course_id, chapter_key)
    )


@router.get("/courses/{course_id}/chapters/{chapter_key}")
async def get_current_chapter(course_id: str, chapter_key: str):
    return _body(
        await _call(course_commands.current_chapter(course_id, chapter_key))
    )


@router.get("/courses/{course_id}/chapters/{chapter_key}/labs")
async def list_current_chapter_labs(course_id: str, chapter_key: str):
    return await _call(CourseService.list_chapter_labs(course_id, chapter_key))


@router.post(
    "/courses/{course_id}/chapters/{chapter_key}/labs/{lab_key}/approve",
    response_model=CourseLabApprovalResponse,
)
async def approve_lab_proposal(
    course_id: str,
    chapter_key: StableKey,
    lab_key: StableKey,
    request: CourseLabApprovalRequest,
):
    return await _call(
        course_v2_service.approve_lab_proposal(
            course_id,
            chapter_key,
            lab_key,
            request,
        )
    )


@router.get("/courses/{course_id}/chapters/{chapter_key}/attempts")
async def list_chapter_attempts(course_id: str, chapter_key: str):
    return await _call(CourseService.list_chapter_attempts(course_id, chapter_key))


@router.post(
    "/courses/{course_id}/chapters/{chapter_key}/labs/{lab_key}/attempts",
    status_code=status.HTTP_201_CREATED,
)
async def create_chapter_attempt(
    course_id: str,
    chapter_key: str,
    lab_key: str,
    request: CourseChapterAttemptCreate,
):
    attempt = await _call(
        CourseService.create_chapter_attempt(
            course_id,
            chapter_key,
            lab_key,
            request.model_dump(exclude_unset=True),
        )
    )
    return _body(attempt)


@router.post("/courses/{course_id}/chapters/{chapter_key}/publish")
async def publish_current_chapter(course_id: str, chapter_key: str):
    return _body(
        await _call(CourseService.publish_current_chapter(course_id, chapter_key))
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


@router.patch("/courses/{course_id}/notes/{note_id}")
async def reattach_note(
    course_id: str, note_id: str, request: CourseNoteReattach
):
    note = await _call(
        CourseService.reattach_note(
            course_id,
            note_id,
            chapter_key=request.chapter_key,
            block_key=request.block_key,
        )
    )
    return _body(note)


@router.delete("/courses/{course_id}/notes/{note_id}")
async def delete_note(course_id: str, note_id: str):
    await _call(CourseService.delete_note(course_id, note_id))
    return {"message": "Note deleted"}
