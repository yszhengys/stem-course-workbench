import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/lib/api/client'
import { courseApi } from './course'

vi.mock('@/lib/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

describe('courseApi', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('submits Source identity and role without a server path', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        command_id: 'command:evidence',
        run_id: 'course_generation_run:evidence',
        status: 'queued',
      },
    })

    await courseApi.buildEvidence('course:one', {
      source_id: 'source:pdf',
      role: 'PRIMARY',
      force: false,
    })

    expect(apiClient.post).toHaveBeenCalledWith(
      '/courses/course%3Aone/evidence/build',
      { source_id: 'source:pdf', role: 'PRIMARY', force: false }
    )
  })

  it('submits the real Open Notebook model record ID', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        command_id: 'command:outline',
        run_id: 'course_generation_run:outline',
        status: 'queued',
      },
    })

    await courseApi.generateOutline('course:one', {
      anchor_ids: ['anchor:one'],
      prompt_version: 'v1',
      force: false,
      model: {
        adapter: 'open_notebook',
        model: 'model:deepseek-v4-pro',
        reasoning_effort: null,
      },
    })

    expect(apiClient.post).toHaveBeenCalledWith(
      '/courses/course%3Aone/outline/generate',
      expect.objectContaining({
        available_lab_keys: expect.any(Array),
        model: expect.objectContaining({ model: 'model:deepseek-v4-pro' }),
      })
    )
    const request = vi.mocked(apiClient.post).mock.calls[0][1] as { available_lab_keys: string[] }
    expect(request.available_lab_keys.length).toBeGreaterThan(0)
    expect(new Set(request.available_lab_keys).size).toBe(request.available_lab_keys.length)
  })

  it('uses stable chapter and Lab keys for attempts and publication', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce({
        data: {
          id: 'attempt:one', lab: 'lab:hidden', answers: { answer: '42' }, status: 'submitted',
          result: null, course: 'course:one', course_version: 'course_version:one',
          chapter: 'chapter:one', chapter_key: 'limits', exercise_key: 'exercise-1',
          answer: null, hints_used: null, answer_revealed: null, transfer_completed: null,
          orphan_status: 'active', created: null, updated: null,
        },
      })
      .mockResolvedValueOnce({
        data: {
          id: 'chapter:one', course_version: 'course_version:one', chapter_no: 1,
          title: 'Limits', chapter_key: 'limits', version_no: 1, artifact: null,
          input_hash: null, status: 'published', published_at: null, content: null,
          review_status: 'passed', validation_status: 'passed', citations: null,
          created: null, updated: null,
        },
      })

    await courseApi.createChapterAttempt('course:one', 'limits', 'lab-1', {
      answers: { answer: '42' }, exercise_key: 'exercise-1',
    })
    await courseApi.publishChapter('course:one', 'limits')

    expect(apiClient.post).toHaveBeenNthCalledWith(
      1,
      '/courses/course%3Aone/chapters/limits/labs/lab-1/attempts',
      { answers: { answer: '42' }, exercise_key: 'exercise-1' }
    )
    expect(apiClient.post).toHaveBeenNthCalledWith(
      2,
      '/courses/course%3Aone/chapters/limits/publish'
    )
  })

  it('submits notes and progress by stable keys without leaking chapter record IDs', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        id: 'course_note:one', course: 'course:one', chapter: 'chapter:resolved-server-side',
        chapter_key: 'limits', block_key: 'definition-limit', orphan_status: 'active',
        content: 'Remember the epsilon condition.', created: null, updated: null,
      },
    })
    vi.mocked(apiClient.put).mockResolvedValue({
      data: {
        id: 'course_progress:one', course: 'course:one', chapter: 'chapter:resolved-server-side',
        chapter_key: 'limits', block_key: null, orphan_status: 'active',
        status: 'completed', created: null, updated: null,
      },
    })

    await courseApi.createNote('course:one', {
      chapter_key: 'limits',
      block_key: 'definition-limit',
      content: 'Remember the epsilon condition.',
      // A stale client must not be able to submit an internal record ID.
      chapter: 'chapter:hidden',
    } as never)
    await courseApi.updateProgress('course:one', {
      chapter_key: 'limits',
      block_key: null,
      status: 'completed',
      chapter: 'chapter:hidden',
    } as never)

    expect(apiClient.post).toHaveBeenCalledWith('/courses/course%3Aone/notes', {
      chapter_key: 'limits',
      block_key: 'definition-limit',
      content: 'Remember the epsilon condition.',
    })
    expect(apiClient.put).toHaveBeenCalledWith('/courses/course%3Aone/progress', {
      chapter_key: 'limits',
      block_key: null,
      status: 'completed',
    })
  })
})
