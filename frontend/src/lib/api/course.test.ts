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

  it('rebuilds generation payloads from runtime allowlists', async () => {
    const stop = new Error('stop after request capture')
    vi.mocked(apiClient.post).mockRejectedValue(stop)
    const request = {
      anchor_ids: ['anchor:one'],
      prompt_version: 'v1',
      force: false,
      model: {
        adapter: 'open_notebook',
        model: 'model:deepseek-v4-pro',
        reasoning_effort: null,
        chapter: 'chapter:hidden',
      },
      escalation_model: {
        adapter: 'codex_cli',
        model: 'gpt-5.6-sol',
        reasoning_effort: 'max',
        prompt: 'hidden',
      },
      file_path: '/private/server/path.pdf',
      evidence_text: 'untrusted legacy evidence',
      chapter: 'chapter:hidden',
    } as never

    await expect(courseApi.generateOutline('course:one', request)).rejects.toBe(stop)
    await expect(courseApi.generateChapter('course:one', 'limits', request)).rejects.toBe(stop)
    await expect(courseApi.reviewChapter('course:one', 'limits', request)).rejects.toBe(stop)

    expect(vi.mocked(apiClient.post).mock.calls[0][1]).toEqual({
      anchor_ids: ['anchor:one'],
      prompt_version: 'v1',
      force: false,
      model: {
        adapter: 'open_notebook',
        model: 'model:deepseek-v4-pro',
        reasoning_effort: null,
      },
      available_lab_keys: expect.any(Array),
    })
    expect(vi.mocked(apiClient.post).mock.calls[1][1]).toEqual({
      anchor_ids: ['anchor:one'],
      prompt_version: 'v1',
      force: false,
      model: {
        adapter: 'open_notebook',
        model: 'model:deepseek-v4-pro',
        reasoning_effort: null,
      },
    })
    expect(vi.mocked(apiClient.post).mock.calls[2][1]).toEqual({
      anchor_ids: ['anchor:one'],
      prompt_version: 'v1',
      force: false,
      model: {
        adapter: 'open_notebook',
        model: 'model:deepseek-v4-pro',
        reasoning_effort: null,
      },
      escalation_model: {
        adapter: 'codex_cli',
        model: 'gpt-5.6-sol',
        reasoning_effort: 'max',
      },
    })
  })

  it('submits independent review and escalation selections exactly', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        command_id: 'command:review',
        run_id: 'course_generation_run:review',
        status: 'queued',
      },
    })

    await courseApi.reviewChapter('course:one', 'limits', {
      anchor_ids: ['anchor:one'],
      prompt_version: 'v1',
      force: false,
      model: {
        adapter: 'codex_cli',
        model: 'gpt-5.6-luna',
        reasoning_effort: 'max',
      },
      escalation_model: {
        adapter: 'codex_cli',
        model: 'gpt-5.6-sol',
        reasoning_effort: 'max',
      },
    })

    expect(apiClient.post).toHaveBeenCalledWith(
      '/courses/course%3Aone/chapters/limits/review',
      {
        anchor_ids: ['anchor:one'],
        prompt_version: 'v1',
        force: false,
        model: {
          adapter: 'codex_cli',
          model: 'gpt-5.6-luna',
          reasoning_effort: 'max',
        },
        escalation_model: {
          adapter: 'codex_cli',
          model: 'gpt-5.6-sol',
          reasoning_effort: 'max',
        },
      }
    )
  })

  it('rebuilds every other Course write payload from runtime allowlists', async () => {
    const stop = new Error('stop after request capture')
    vi.mocked(apiClient.post).mockRejectedValue(stop)
    vi.mocked(apiClient.patch).mockRejectedValue(stop)
    vi.mocked(apiClient.put).mockRejectedValue(stop)

    await expect(courseApi.create({
      title: 'Calculus',
      subject: 'math',
      description: null,
      language: 'en-US',
      notebook_id: 'notebook:one',
      file_path: '/private/server/course.pdf',
    } as never)).rejects.toBe(stop)
    await expect(courseApi.associateSource('course:one', {
      source_id: 'source:one',
      role: 'PRIMARY',
      chapter: 'chapter:hidden',
    } as never)).rejects.toBe(stop)
    await expect(courseApi.buildEvidence('course:one', {
      source_id: 'source:one',
      role: 'PRIMARY',
      force: false,
      file_path: '/private/server/source.pdf',
      evidence_text: 'legacy evidence',
    } as never)).rejects.toBe(stop)
    await expect(courseApi.approveOutline('course:one', {
      version_id: 'course_outline_version:one',
      confirmation: '确认大纲',
      chapter: 'chapter:hidden',
    } as never)).rejects.toBe(stop)
    await expect(courseApi.createChapterAttempt('course:one', 'limits', 'lab-1', {
      answers: { value: 42 },
      exercise_key: 'exercise-1',
      answer: '42',
      hints_used: 1,
      answer_revealed: false,
      transfer_completed: true,
      chapter: 'chapter:hidden',
    } as never)).rejects.toBe(stop)
    await expect(courseApi.createNote('course:one', {
      chapter_key: 'limits',
      block_key: 'definition-limit',
      content: 'Remember this.',
      chapter: 'chapter:hidden',
    } as never)).rejects.toBe(stop)

    expect(vi.mocked(apiClient.post).mock.calls.map(([path, payload]) => [path, payload])).toEqual([
      ['/courses', {
        title: 'Calculus',
        subject: 'math',
        description: null,
        language: 'en-US',
        notebook_id: 'notebook:one',
      }],
      ['/courses/course%3Aone/sources', { source_id: 'source:one', role: 'PRIMARY' }],
      ['/courses/course%3Aone/evidence/build', { source_id: 'source:one', role: 'PRIMARY', force: false }],
      ['/courses/course%3Aone/outline/approve', {
        version_id: 'course_outline_version:one',
        confirmation: '确认大纲',
      }],
      ['/courses/course%3Aone/chapters/limits/labs/lab-1/attempts', {
        answers: { value: 42 },
        exercise_key: 'exercise-1',
        answer: '42',
        hints_used: 1,
        answer_revealed: false,
        transfer_completed: true,
      }],
      ['/courses/course%3Aone/notes', {
        chapter_key: 'limits',
        block_key: 'definition-limit',
        content: 'Remember this.',
      }],
    ])

    await expect(courseApi.updateFinding('course:one', 'finding:one', {
      status: 'resolved',
      resolution_reason: 'Checked manually.',
      evidence_text: 'legacy evidence',
    } as never)).rejects.toBe(stop)
    await expect(courseApi.reattachNote('course:one', 'note:one', {
      chapter_key: 'limits',
      block_key: 'definition-limit',
      chapter: 'chapter:hidden',
    } as never)).rejects.toBe(stop)

    expect(vi.mocked(apiClient.patch).mock.calls.map(([path, payload]) => [path, payload])).toEqual([
      ['/courses/course%3Aone/findings/finding%3Aone', {
        status: 'resolved',
        resolution_reason: 'Checked manually.',
      }],
      ['/courses/course%3Aone/notes/note%3Aone', {
        chapter_key: 'limits',
        block_key: 'definition-limit',
      }],
    ])

    await expect(courseApi.updateProgress('course:one', {
      chapter_key: 'limits',
      block_key: null,
      status: 'completed',
      chapter: 'chapter:hidden',
    } as never)).rejects.toBe(stop)
    expect(apiClient.put).toHaveBeenCalledWith('/courses/course%3Aone/progress', {
      chapter_key: 'limits',
      block_key: null,
      status: 'completed',
    })
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
