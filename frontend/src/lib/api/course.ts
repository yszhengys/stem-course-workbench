import { z } from 'zod'

import { apiClient } from '@/lib/api/client'
import { SAFE_LAB_PROPOSAL_KEYS } from '@/lib/course/lab-proposals'
import {
  BuildEvidenceRequest,
  courseDraftOperationRequestSchema,
  courseDraftResponseSchema,
  courseDraftValidationResponseSchema,
  courseBundleImportResponseSchema,
  courseExportResponseSchema,
  courseAttemptSchema,
  courseAttemptWithLabSchema,
  courseExerciseGradeResponseSchema,
  courseExerciseBuildStatusSchema,
  courseExerciseGradeRequestSchema,
  courseExerciseHintRequestSchema,
  courseExerciseHintResponseSchema,
  courseExerciseRevealRequestSchema,
  courseExerciseRevealResponseSchema,
  courseExerciseVerificationRequestSchema,
  exerciseVerificationSchema,
  courseExerciseSchema,
  courseFindingSchema,
  courseJobSchema,
  courseLabSchema,
  courseLearningEventResponseSchema,
  courseLearningEventRequestSchema,
  courseLearningUpgradeRequestSchema,
  courseLearningUpgradeResponseSchema,
  courseLearningOverviewSchema,
  courseLearnerChapterResponseSchema,
  courseLearnerNoteCreateRequestSchema,
  courseLearnerNoteSchema,
  courseLearnerNotesResponseSchema,
  courseLearnerSourcesResponseSchema,
  courseModelOptionsSchema,
  courseNoteSchema,
  courseSchema,
  courseVersionSchema,
  courseTransferGradeRequestSchema,
  courseTutorMessageRequestSchema,
  courseTutorMessageResponseSchema,
  courseTutorSessionCreateRequestSchema,
  courseTutorSessionSchema,
  CreateCourseRequest,
  CreateCourseAttemptRequest,
  CourseExerciseGradeRequest,
  CourseExerciseHintRequest,
  CourseExerciseRevealRequest,
  CourseExerciseVerificationRequest,
  CourseDraftOperationRequest,
  CourseLearningEventRequest,
  CourseLearningUpgradeRequest,
  CourseLearnerNoteCreateRequest,
  CourseTransferGradeRequest,
  CourseTutorMessageRequest,
  CourseTutorSessionCreateRequest,
  eligibleCourseSourceSchema,
  evidenceAnchorSchema,
  GenerateChapterRequest,
  GenerateExerciseBankRequest,
  GenerateOutlineRequest,
  ReviewChapterRequest,
  chapterSchema,
  ModelSelection,
  progressSchema,
  reviewQueueItemSchema,
} from '@/lib/types/course'

const pathId = encodeURIComponent

function modelPayload(model: ModelSelection): ModelSelection {
  return {
    adapter: model.adapter,
    model: model.model,
    reasoning_effort: model.reasoning_effort,
  }
}

function anchoredJobPayload(request: GenerateOutlineRequest | GenerateChapterRequest) {
  return {
    anchor_ids: [...request.anchor_ids],
    prompt_version: request.prompt_version,
    model: modelPayload(request.model),
    force: request.force,
  }
}

export const courseApi = {
  async list() {
    const response = await apiClient.get('/courses')
    return z.array(courseSchema).parse(response.data)
  },

  async get(courseId: string) {
    const response = await apiClient.get(`/courses/${pathId(courseId)}`)
    return courseSchema.parse(response.data)
  },

  async prepareLearningUpgrade(
    courseId: string,
    request: CourseLearningUpgradeRequest,
  ) {
    const parsed = courseLearningUpgradeRequestSchema.parse(request)
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/versions/prepare-learning-upgrade`,
      parsed,
    )
    return courseLearningUpgradeResponseSchema.parse(response.data)
  },

  async create(request: CreateCourseRequest) {
    const payload: CreateCourseRequest = {
      title: request.title,
      language: request.language,
    }
    if (request.subject !== undefined) payload.subject = request.subject
    if (request.description !== undefined) payload.description = request.description
    if (request.notebook_id !== undefined) payload.notebook_id = request.notebook_id
    const response = await apiClient.post('/courses', payload)
    return courseSchema.parse(response.data)
  },

  async createExport(courseId: string, includeOriginals: boolean) {
    const response = await apiClient.post(`/courses/${pathId(courseId)}/exports`, {
      include_originals: includeOriginals,
    })
    return courseExportResponseSchema.parse(response.data)
  },

  async getExport(courseId: string, exportId: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/exports/${pathId(exportId)}`,
    )
    return courseExportResponseSchema.parse(response.data)
  },

  async downloadExport(courseId: string, exportId: string, filename: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/exports/${pathId(exportId)}/download`,
      { responseType: 'blob' },
    )
    const objectUrl = URL.createObjectURL(response.data as Blob)
    const anchor = document.createElement('a')
    try {
      anchor.href = objectUrl
      anchor.download = filename
      anchor.click()
    } finally {
      URL.revokeObjectURL(objectUrl)
    }
  },

  async importBundle(bundle: File) {
    const form = new FormData()
    form.append('bundle', bundle)
    const response = await apiClient.post('/courses/imports', form)
    return courseBundleImportResponseSchema.parse(response.data)
  },

  async listEligibleSources(courseId: string) {
    const response = await apiClient.get(`/courses/${pathId(courseId)}/sources/eligible`)
    return z.array(eligibleCourseSourceSchema).parse(response.data)
  },

  async associateSource(courseId: string, request: { source_id: string; role: 'PRIMARY' | 'SUPPLEMENT' }) {
    const response = await apiClient.post(`/courses/${pathId(courseId)}/sources`, {
      source_id: request.source_id,
      role: request.role,
    })
    return courseSchema.parse(response.data)
  },

  async buildEvidence(courseId: string, request: BuildEvidenceRequest) {
    const response = await apiClient.post(`/courses/${pathId(courseId)}/evidence/build`, {
      source_id: request.source_id,
      role: request.role,
      force: request.force,
    })
    return courseJobSchema.parse(response.data)
  },

  async listAnchors(courseId: string) {
    const response = await apiClient.get(`/courses/${pathId(courseId)}/evidence/anchors`)
    return z.array(evidenceAnchorSchema).parse(response.data)
  },

  async getEvidencePreviewBlob(courseId: string, anchorId: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/evidence/anchors/${pathId(anchorId)}/preview`,
      { responseType: 'blob' },
    )
    return response.data as Blob
  },

  async getEvidenceSourceBlob(courseId: string, anchorId: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/evidence/anchors/${pathId(anchorId)}/source`,
      { responseType: 'blob' },
    )
    return response.data as Blob
  },

  async getModelOptions() {
    const response = await apiClient.get('/courses/model-options')
    return courseModelOptionsSchema.parse(response.data)
  },

  async generateOutline(courseId: string, request: GenerateOutlineRequest) {
    const payload = anchoredJobPayload(request)
    const response = await apiClient.post(`/courses/${pathId(courseId)}/outline/generate`, {
      anchor_ids: payload.anchor_ids,
      prompt_version: payload.prompt_version,
      model: payload.model,
      force: payload.force,
      available_lab_keys: [...SAFE_LAB_PROPOSAL_KEYS],
    })
    return courseJobSchema.parse(response.data)
  },

  async getCurrentOutline(courseId: string) {
    const response = await apiClient.get(`/courses/${pathId(courseId)}/outline/current`)
    return courseVersionSchema.parse(response.data)
  },

  async approveOutline(courseId: string, request: { version_id: string; confirmation: string }) {
    const response = await apiClient.post(`/courses/${pathId(courseId)}/outline/approve`, {
      version_id: request.version_id,
      confirmation: request.confirmation,
    })
    return courseVersionSchema.parse(response.data)
  },

  async generateChapter(courseId: string, chapterKey: string, request: GenerateChapterRequest) {
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/generate`,
      anchoredJobPayload(request)
    )
    return courseJobSchema.parse(response.data)
  },

  async reviewChapter(courseId: string, chapterKey: string, request: ReviewChapterRequest) {
    const payload = anchoredJobPayload(request)
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/review`,
      {
        ...payload,
        escalation_model: modelPayload(request.escalation_model),
      }
    )
    return courseJobSchema.parse(response.data)
  },

  async generateExerciseBank(
    courseId: string,
    chapterKey: string,
    request: GenerateExerciseBankRequest,
  ) {
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/exercises/generate`,
      {
        ...anchoredJobPayload(request),
        review_model: modelPayload(request.review_model),
      },
    )
    return courseJobSchema.parse(response.data)
  },

  async getExerciseBuildStatus(courseId: string, chapterKey: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/exercises/build-status`,
    )
    return courseExerciseBuildStatusSchema.parse(response.data)
  },

  async verifyExercise(
    courseId: string,
    chapterKey: string,
    exerciseKey: string,
    request: CourseExerciseVerificationRequest,
  ) {
    const parsed = courseExerciseVerificationRequestSchema.parse(request)
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/exercises/${pathId(exerciseKey)}/verify`,
      parsed,
    )
    return exerciseVerificationSchema.parse(response.data)
  },

  async getCurrentChapter(courseId: string, chapterKey: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}`
    )
    return chapterSchema.parse(response.data)
  },

  async getLearningOverview(courseId: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/learning/overview`,
    )
    return courseLearningOverviewSchema.parse(response.data)
  },

  async getLearningChapter(courseId: string, chapterKey: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/learning/chapters/${pathId(chapterKey)}`,
    )
    return courseLearnerChapterResponseSchema.parse(response.data)
  },

  async getLearningSources(courseId: string, chapterKey: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/learning/chapters/${pathId(chapterKey)}/sources`,
    )
    return courseLearnerSourcesResponseSchema.parse(response.data)
  },

  async getLearningNotes(courseId: string, chapterKey: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/learning/chapters/${pathId(chapterKey)}/notes`,
    )
    return courseLearnerNotesResponseSchema.parse(response.data)
  },

  async createLearningNote(
    courseId: string,
    chapterKey: string,
    request: CourseLearnerNoteCreateRequest,
  ) {
    const parsed = courseLearnerNoteCreateRequestSchema.parse(request)
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/learning/chapters/${pathId(chapterKey)}/notes`,
      {
        snapshot_token: parsed.snapshot_token,
        block_key: parsed.block_key,
        content: parsed.content,
      },
    )
    return courseLearnerNoteSchema.parse(response.data)
  },

  async listTutorSessions(courseId: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/tutor/sessions`,
    )
    return z.array(courseTutorSessionSchema).parse(response.data)
  },

  async createTutorSession(
    courseId: string,
    request: CourseTutorSessionCreateRequest,
  ) {
    const parsed = courseTutorSessionCreateRequestSchema.parse(request)
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/tutor/sessions`,
      {
        snapshot_token: parsed.snapshot_token,
        chapter_key: parsed.chapter_key,
        model: modelPayload(parsed.model),
      },
    )
    return courseTutorSessionSchema.parse(response.data)
  },

  async sendTutorMessage(
    courseId: string,
    sessionId: string,
    request: CourseTutorMessageRequest,
  ) {
    const parsed = courseTutorMessageRequestSchema.parse(request)
    const payload: CourseTutorMessageRequest = {
      snapshot_token: parsed.snapshot_token,
      idempotency_key: parsed.idempotency_key,
      content: parsed.content,
      intent: parsed.intent,
    }
    if (parsed.exercise_key !== undefined) payload.exercise_key = parsed.exercise_key
    if (parsed.concept_key !== undefined) payload.concept_key = parsed.concept_key
    if (parsed.attempt_key !== undefined) payload.attempt_key = parsed.attempt_key
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/tutor/sessions/${pathId(sessionId)}/messages`,
      payload,
    )
    return courseTutorMessageResponseSchema.parse(response.data)
  },

  async getChapterDraft(courseId: string, chapterKey: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/draft`,
    )
    return courseDraftResponseSchema.parse(response.data)
  },

  async applyChapterDraftOperation(
    courseId: string,
    chapterKey: string,
    request: CourseDraftOperationRequest,
  ) {
    const parsed = courseDraftOperationRequestSchema.parse(request)
    const response = await apiClient.patch(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/draft`,
      {
        revision_token: parsed.revision_token,
        operation: parsed.operation,
      },
    )
    return courseDraftResponseSchema.parse(response.data)
  },

  async validateChapterDraft(
    courseId: string,
    chapterKey: string,
    request: { revision_token: string },
  ) {
    const parsed = z.object({ revision_token: z.string().regex(/^[0-9a-f]{64}$/) })
      .strict().parse(request)
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/draft/validate`,
      { revision_token: parsed.revision_token },
    )
    return courseDraftValidationResponseSchema.parse(response.data)
  },

  async getReviewQueue(courseId: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/learning/review-queue`,
    )
    return z.array(reviewQueueItemSchema).parse(response.data)
  },

  async appendLearningEvent(courseId: string, request: CourseLearningEventRequest) {
    const parsed = courseLearningEventRequestSchema.parse(request)
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/learning/events`,
      {
        snapshot_token: parsed.snapshot_token,
        idempotency_key: parsed.idempotency_key,
        chapter_key: parsed.chapter_key,
        kind: parsed.kind,
        payload: parsed.payload,
      },
    )
    return courseLearningEventResponseSchema.parse(response.data)
  },

  async listLearningExercises(courseId: string, chapterKey?: string) {
    const response = await apiClient.get(`/courses/${pathId(courseId)}/exercises`, {
      params: chapterKey ? { chapter_key: chapterKey } : undefined,
    })
    return z.array(courseExerciseSchema).parse(response.data)
  },

  async gradeLearningExercise(
    courseId: string,
    exerciseKey: string,
    request: CourseExerciseGradeRequest,
  ) {
    const parsed = courseExerciseGradeRequestSchema.parse(request)
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/exercises/${pathId(exerciseKey)}/grade`,
      {
        snapshot_token: parsed.snapshot_token,
        chapter_key: parsed.chapter_key,
        concept_key: parsed.concept_key,
        attempt_key: parsed.attempt_key,
        answer: parsed.answer,
        hints_used: parsed.hints_used,
        answer_revealed: parsed.answer_revealed,
        mode: parsed.mode,
      },
    )
    return courseExerciseGradeResponseSchema.parse(response.data)
  },

  async requestNextHint(
    courseId: string,
    exerciseKey: string,
    request: CourseExerciseHintRequest,
  ) {
    const parsed = courseExerciseHintRequestSchema.parse(request)
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/exercises/${pathId(exerciseKey)}/hints/next`,
      {
        snapshot_token: parsed.snapshot_token,
        idempotency_key: parsed.idempotency_key,
        chapter_key: parsed.chapter_key,
        concept_key: parsed.concept_key,
        attempt_key: parsed.attempt_key,
        hint_index: parsed.hint_index,
      },
    )
    return courseExerciseHintResponseSchema.parse(response.data)
  },

  async revealExerciseAnswer(
    courseId: string,
    exerciseKey: string,
    request: CourseExerciseRevealRequest,
  ) {
    const parsed = courseExerciseRevealRequestSchema.parse(request)
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/exercises/${pathId(exerciseKey)}/reveal`,
      {
        snapshot_token: parsed.snapshot_token,
        idempotency_key: parsed.idempotency_key,
        chapter_key: parsed.chapter_key,
        concept_key: parsed.concept_key,
        attempt_key: parsed.attempt_key,
      },
    )
    return courseExerciseRevealResponseSchema.parse(response.data)
  },

  async gradeTransfer(
    courseId: string,
    exerciseKey: string,
    request: CourseTransferGradeRequest,
  ) {
    const parsed = courseTransferGradeRequestSchema.parse(request)
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/exercises/${pathId(exerciseKey)}/transfer/grade`,
      {
        snapshot_token: parsed.snapshot_token,
        chapter_key: parsed.chapter_key,
        concept_key: parsed.concept_key,
        source_attempt_key: parsed.source_attempt_key,
        attempt_key: parsed.attempt_key,
        transfer_task_key: parsed.transfer_task_key,
        answer: parsed.answer,
      },
    )
    return courseExerciseGradeResponseSchema.parse(response.data)
  },

  async listChapterLabs(courseId: string, chapterKey: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/labs`
    )
    return z.array(courseLabSchema).parse(response.data)
  },

  async listChapterAttempts(courseId: string, chapterKey: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/attempts`
    )
    return z.array(courseAttemptWithLabSchema).parse(response.data)
  },

  async createChapterAttempt(
    courseId: string,
    chapterKey: string,
    labKey: string,
    request: CreateCourseAttemptRequest
  ) {
    const payload: CreateCourseAttemptRequest = {
      answers: { ...request.answers },
    }
    if (request.exercise_key !== undefined) payload.exercise_key = request.exercise_key
    if (request.answer !== undefined) payload.answer = request.answer
    if (request.hints_used !== undefined) payload.hints_used = request.hints_used
    if (request.answer_revealed !== undefined) payload.answer_revealed = request.answer_revealed
    if (request.transfer_completed !== undefined) payload.transfer_completed = request.transfer_completed
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/labs/${pathId(labKey)}/attempts`,
      payload
    )
    return courseAttemptSchema.parse(response.data)
  },

  async publishChapter(courseId: string, chapterKey: string) {
    const response = await apiClient.post(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}/publish`
    )
    return chapterSchema.parse(response.data)
  },

  async listFindings(courseId: string, chapterKey?: string) {
    const response = await apiClient.get(`/courses/${pathId(courseId)}/findings`, {
      params: chapterKey ? { chapter_key: chapterKey } : undefined,
    })
    return z.array(courseFindingSchema).parse(response.data)
  },

  async updateFinding(
    courseId: string,
    findingId: string,
    request: { status: 'resolved' | 'acknowledged'; resolution_reason: string }
  ) {
    const response = await apiClient.patch(
      `/courses/${pathId(courseId)}/findings/${pathId(findingId)}`,
      {
        status: request.status,
        resolution_reason: request.resolution_reason,
      }
    )
    return courseFindingSchema.parse(response.data)
  },

  async listProgress(courseId: string) {
    const response = await apiClient.get(`/courses/${pathId(courseId)}/progress`)
    return z.array(progressSchema).parse(response.data)
  },

  async updateProgress(courseId: string, request: {
    chapter_key?: string | null
    block_key?: string | null
    status: string
  }) {
    const payload = {
      chapter_key: request.chapter_key ?? null,
      block_key: request.block_key ?? null,
      status: request.status,
    }
    const response = await apiClient.put(`/courses/${pathId(courseId)}/progress`, payload)
    return progressSchema.parse(response.data)
  },

  async listNotes(courseId: string) {
    const response = await apiClient.get(`/courses/${pathId(courseId)}/notes`)
    return z.array(courseNoteSchema).parse(response.data)
  },

  async createNote(courseId: string, request: {
    chapter_key?: string | null
    block_key?: string | null
    content: string
  }) {
    const payload = {
      chapter_key: request.chapter_key ?? null,
      block_key: request.block_key ?? null,
      content: request.content,
    }
    const response = await apiClient.post(`/courses/${pathId(courseId)}/notes`, payload)
    return courseNoteSchema.parse(response.data)
  },

  async deleteNote(courseId: string, noteId: string) {
    await apiClient.delete(`/courses/${pathId(courseId)}/notes/${pathId(noteId)}`)
  },

  async reattachNote(
    courseId: string,
    noteId: string,
    request: { chapter_key: string; block_key: string }
  ) {
    const response = await apiClient.patch(
      `/courses/${pathId(courseId)}/notes/${pathId(noteId)}`,
      {
        chapter_key: request.chapter_key,
        block_key: request.block_key,
      }
    )
    return courseNoteSchema.parse(response.data)
  },

  async retrievalContext(courseId: string, anchorIds: string[]) {
    const response = await apiClient.post(`/courses/${pathId(courseId)}/retrieval/context`, {
      anchor_ids: anchorIds,
    })
    return z.object({
      course_id: z.string(),
      anchor_ids: z.array(z.string()),
      context: z.array(z.string()),
    }).strict().parse(response.data)
  },
}
