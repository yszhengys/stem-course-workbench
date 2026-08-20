import { z } from 'zod'

import { apiClient } from '@/lib/api/client'
import { SAFE_LAB_PROPOSAL_KEYS } from '@/lib/course/lab-proposals'
import {
  BuildEvidenceRequest,
  courseAttemptSchema,
  courseAttemptWithLabSchema,
  courseFindingSchema,
  courseJobSchema,
  courseLabSchema,
  courseModelOptionsSchema,
  courseNoteSchema,
  courseSchema,
  courseVersionSchema,
  CreateCourseRequest,
  CreateCourseAttemptRequest,
  eligibleCourseSourceSchema,
  evidenceAnchorSchema,
  GenerateChapterRequest,
  GenerateOutlineRequest,
  ReviewChapterRequest,
  chapterSchema,
  ModelSelection,
  progressSchema,
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

  async getCurrentChapter(courseId: string, chapterKey: string) {
    const response = await apiClient.get(
      `/courses/${pathId(courseId)}/chapters/${pathId(chapterKey)}`
    )
    return chapterSchema.parse(response.data)
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
