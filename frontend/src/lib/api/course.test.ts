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

  it('loads evidence preview and source files through the authenticated API client', async () => {
    const preview = new Blob(['preview'], { type: 'image/svg+xml' })
    const source = new Blob(['source'], { type: 'application/pdf' })
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: preview })
      .mockResolvedValueOnce({ data: source })

    await expect(
      courseApi.getEvidencePreviewBlob('course:one', 'anchor:page/1')
    ).resolves.toBe(preview)
    await expect(
      courseApi.getEvidenceSourceBlob('course:one', 'anchor:page/1')
    ).resolves.toBe(source)

    expect(apiClient.get).toHaveBeenNthCalledWith(
      1,
      '/courses/course%3Aone/evidence/anchors/anchor%3Apage%2F1/preview',
      { responseType: 'blob' },
    )
    expect(apiClient.get).toHaveBeenNthCalledWith(
      2,
      '/courses/course%3Aone/evidence/anchors/anchor%3Apage%2F1/source',
      { responseType: 'blob' },
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

  it('encodes learner routes and never forwards hidden exercise fields', async () => {
    const stop = new Error('stop after request capture')
    vi.mocked(apiClient.get).mockRejectedValue(stop)
    vi.mocked(apiClient.post).mockRejectedValue(stop)

    await expect(courseApi.listLearningExercises('course:one', 'limits/intro'))
      .rejects.toBe(stop)
    await expect(courseApi.appendLearningEvent('course:one', {
      snapshot_token: 'a'.repeat(64),
      idempotency_key: 'event-one',
      chapter_key: 'limits',
      kind: 'chapter_opened',
      payload: { block_key: null },
    })).rejects.toBe(stop)
    await expect(courseApi.gradeLearningExercise('course:one', 'limits/core', {
      snapshot_token: 'b'.repeat(64),
      chapter_key: 'limits',
      concept_key: 'limit-laws',
      attempt_key: 'attempt-one',
      answer: '4',
      hints_used: 1,
      answer_revealed: false,
      mode: 'practice',
      course_version_id: 'course_version:hidden',
      grader: { oracle_answer: 4 },
    } as never)).rejects.toThrow()
    await expect(courseApi.gradeLearningExercise('course:one', 'limits/core', {
      snapshot_token: 'b'.repeat(64),
      chapter_key: 'limits',
      concept_key: 'limit-laws',
      attempt_key: 'attempt-one',
      answer: '4',
      hints_used: 1,
      answer_revealed: false,
      mode: 'practice',
    })).rejects.toBe(stop)

    expect(apiClient.get).toHaveBeenCalledWith('/courses/course%3Aone/exercises', {
      params: { chapter_key: 'limits/intro' },
    })
    expect(apiClient.post).toHaveBeenNthCalledWith(
      1,
      '/courses/course%3Aone/learning/events',
      {
        idempotency_key: 'event-one',
        chapter_key: 'limits',
        snapshot_token: 'a'.repeat(64),
        kind: 'chapter_opened',
        payload: { block_key: null },
      },
    )
    expect(apiClient.post).toHaveBeenNthCalledWith(
      2,
      '/courses/course%3Aone/exercises/limits%2Fcore/grade',
      {
        snapshot_token: 'b'.repeat(64),
        chapter_key: 'limits',
        concept_key: 'limit-laws',
        attempt_key: 'attempt-one',
        answer: '4',
        hints_used: 1,
        answer_revealed: false,
        mode: 'practice',
      },
    )
  })

  it('rejects mismatched or extended learning event payloads before transport', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        event: {
          event_id: 'event-one',
          course_id: 'course:one',
          course_version_id: 'course_version:published',
          chapter_key: 'limits',
          concept_key: 'limit-laws',
          exercise_key: 'limits-core',
          kind: 'hint_viewed',
          payload: { attempt_key: 'attempt-one', hint_index: 1 },
          occurred_at: '2026-08-22T08:00:00Z',
        },
        mastery: null,
      },
    })

    await expect(courseApi.appendLearningEvent('course:one', {
      snapshot_token: 'a'.repeat(64),
      idempotency_key: 'event-one',
      chapter_key: 'limits',
      kind: 'chapter_opened',
      payload: { block_key: 'definition', attempt_key: 'forged' },
    } as never)).rejects.toThrow()
    await expect(courseApi.appendLearningEvent('course:one', {
      snapshot_token: 'a'.repeat(64),
      idempotency_key: 'event-two',
      chapter_key: 'limits',
      concept_key: 'limit-laws',
      exercise_key: 'limits-core',
      kind: 'hint_viewed',
      payload: { attempt_key: 'attempt-one', hint_index: 1, grader: 'hidden' },
    } as never)).rejects.toThrow()

    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('loads only the learner-safe published chapter projection', async () => {
    const chapter = {
      course_id: 'course:one',
      course_version_id: 'course_version:published',
      chapter_key: 'limits',
      chapter_no: 1,
      title: 'Limits',
      status: 'published',
      snapshot_token: 'a'.repeat(64),
      artifact: {
        purpose: 'Understand limits.',
        prerequisites: [],
        objectives: ['Evaluate limits'],
        sections: [{
          block_key: 'definition', title: 'Definition', markdown: 'Grounded.',
          anchor_ids: ['anchor:one'], provenance: 'adapted',
        }],
        definitions: [], formulas: [], worked_examples: [], misconceptions: [],
        pitfalls: [], quick_reference: [], citations: ['anchor:one'],
      },
    }
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: chapter })

    await expect(courseApi.getLearningChapter('course:one', 'limits/intro'))
      .resolves.toEqual(chapter)
    expect(apiClient.get).toHaveBeenCalledWith(
      '/courses/course%3Aone/learning/chapters/limits%2Fintro',
    )

    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        ...chapter,
        artifact: {
          ...chapter.artifact,
          exercises: [{ answer: 'secret', hints: ['secret'] }],
        },
      },
    })
    await expect(courseApi.getLearningChapter('course:one', 'limits'))
      .rejects.toThrow()
  })

  it('uses dedicated snapshot-bound hint, reveal, grade and transfer routes', async () => {
    const stop = new Error('stop after request capture')
    vi.mocked(apiClient.post).mockRejectedValue(stop)
    const action = {
      snapshot_token: 'a'.repeat(64),
      chapter_key: 'limits',
      concept_key: 'limit-laws',
      attempt_key: 'attempt-one',
    }

    await expect(courseApi.requestNextHint('course:one', 'limits/core', {
      ...action, idempotency_key: 'hint-one', hint_index: 1,
    })).rejects.toBe(stop)
    await expect(courseApi.revealExerciseAnswer('course:one', 'limits/core', {
      ...action, idempotency_key: 'reveal-one',
    })).rejects.toBe(stop)
    await expect(courseApi.gradeLearningExercise('course:one', 'limits/core', {
      ...action, answer: { value: '4' }, hints_used: 1,
      answer_revealed: false, mode: 'practice',
    })).rejects.toBe(stop)
    await expect(courseApi.gradeTransfer('course:one', 'limits/core', {
      ...action, source_attempt_key: 'attempt-one', attempt_key: 'transfer-one',
      transfer_task_key: 'limits-transfer', answer: { value: '4' },
    })).rejects.toBe(stop)

    expect(vi.mocked(apiClient.post).mock.calls).toEqual([
      ['/courses/course%3Aone/exercises/limits%2Fcore/hints/next', {
        ...action, idempotency_key: 'hint-one', hint_index: 1,
      }],
      ['/courses/course%3Aone/exercises/limits%2Fcore/reveal', {
        ...action, idempotency_key: 'reveal-one',
      }],
      ['/courses/course%3Aone/exercises/limits%2Fcore/grade', {
        ...action, answer: { value: '4' }, hints_used: 1,
        answer_revealed: false, mode: 'practice',
      }],
      ['/courses/course%3Aone/exercises/limits%2Fcore/transfer/grade', {
        ...action, source_attempt_key: 'attempt-one', attempt_key: 'transfer-one',
        transfer_task_key: 'limits-transfer', answer: { value: '4' },
      }],
    ])
  })

  it('rejects client-injected grader or record IDs before learner actions', async () => {
    const base = {
      snapshot_token: 'a'.repeat(64), chapter_key: 'limits',
      concept_key: 'limit-laws', attempt_key: 'attempt-one',
    }

    await expect(courseApi.gradeLearningExercise('course:one', 'limits-core', {
      ...base, answer: '4', hints_used: 0, answer_revealed: false, mode: 'practice',
      exercise_id: 'course_exercise:foreign',
    } as never)).rejects.toThrow()
    await expect(courseApi.requestNextHint('course:one', 'limits-core', {
      ...base, idempotency_key: 'hint-one', hint_index: 1,
      grader: { oracle_answer: 4 },
    } as never)).rejects.toThrow()
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('loads version-scoped sources and notes and creates a snapshot-bound note', async () => {
    const sources = {
      snapshot_token: 'a'.repeat(64),
      sources: [{
        anchor_id: 'anchor:one', filename: 'course.pdf', kind: 'pdf_page',
        index: 2, quote: 'Grounded excerpt.', source_role: 'PRIMARY', bbox: null,
      }],
    }
    const notes = {
      snapshot_token: 'a'.repeat(64),
      notes: [{
        note_id: 'course_note:one', block_key: 'definition', content: 'Remember.',
        orphan_status: 'active', created: null,
      }],
    }
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: sources })
      .mockResolvedValueOnce({ data: notes })
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: notes.notes[0] })

    await expect(courseApi.getLearningSources('course:one', 'limits/intro'))
      .resolves.toEqual(sources)
    await expect(courseApi.getLearningNotes('course:one', 'limits/intro'))
      .resolves.toEqual(notes)
    await expect(courseApi.createLearningNote('course:one', 'limits/intro', {
      snapshot_token: 'a'.repeat(64),
      block_key: 'definition',
      content: 'Remember.',
    })).resolves.toEqual(notes.notes[0])

    expect(apiClient.get).toHaveBeenNthCalledWith(
      1, '/courses/course%3Aone/learning/chapters/limits%2Fintro/sources',
    )
    expect(apiClient.get).toHaveBeenNthCalledWith(
      2, '/courses/course%3Aone/learning/chapters/limits%2Fintro/notes',
    )
    expect(apiClient.post).toHaveBeenCalledWith(
      '/courses/course%3Aone/learning/chapters/limits%2Fintro/notes',
      {
        snapshot_token: 'a'.repeat(64),
        block_key: 'definition',
        content: 'Remember.',
      },
    )
  })

  it('rejects a record ID injected into a learner note before transport', async () => {
    await expect(courseApi.createLearningNote('course:one', 'limits', {
      snapshot_token: 'a'.repeat(64), block_key: 'definition', content: 'Remember.',
      chapter_id: 'chapter:foreign',
    } as never)).rejects.toThrow()
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('uses version-bound tutor routes without sending client-selected evidence', async () => {
    const session = {
      session_id: 'course_tutor_session:one',
      course_version_id: 'course_version:published',
      chapter_key: 'limits',
      model: {
        adapter: 'open_notebook' as const, model: 'model:teacher', reasoning_effort: null,
      },
      status: 'active', turns: [], created: '2026-08-22T08:00:00Z',
    }
    const response = {
      snapshot_token: 'a'.repeat(64),
      response: {
        session_id: session.session_id,
        turn: {
          turn_no: 2, role: 'assistant', content: 'Use the definition [1].',
          anchor_ids: ['anchor:one'], answer_revealed: false,
        },
        insufficient_evidence: false,
      },
    }
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: [session] })
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce({ data: session })
      .mockResolvedValueOnce({ data: response })

    await expect(courseApi.listTutorSessions('course:one')).resolves.toEqual([session])
    await expect(courseApi.createTutorSession('course:one', {
      snapshot_token: 'a'.repeat(64), chapter_key: 'limits', model: session.model,
    })).resolves.toEqual(session)
    await expect(courseApi.sendTutorMessage('course:one', session.session_id, {
      snapshot_token: 'a'.repeat(64), idempotency_key: 'message-one',
      content: 'Explain this step.', intent: 'explain',
    })).resolves.toEqual(response)

    expect(apiClient.get).toHaveBeenCalledWith(
      '/courses/course%3Aone/tutor/sessions',
    )
    expect(apiClient.post).toHaveBeenNthCalledWith(
      1, '/courses/course%3Aone/tutor/sessions', {
        snapshot_token: 'a'.repeat(64), chapter_key: 'limits', model: session.model,
      },
    )
    expect(apiClient.post).toHaveBeenNthCalledWith(
      2,
      '/courses/course%3Aone/tutor/sessions/course_tutor_session%3Aone/messages',
      {
        snapshot_token: 'a'.repeat(64), idempotency_key: 'message-one',
        content: 'Explain this step.', intent: 'explain',
      },
    )
  })

  it('rejects tutor record IDs and evidence injected by the client', async () => {
    await expect(courseApi.createTutorSession('course:one', {
      snapshot_token: 'a'.repeat(64), chapter_key: 'limits',
      model: { adapter: 'open_notebook', model: 'model:teacher', reasoning_effort: null },
      course_version_id: 'course_version:foreign',
    } as never)).rejects.toThrow()
    await expect(courseApi.sendTutorMessage(
      'course:one', 'course_tutor_session:one', {
        snapshot_token: 'a'.repeat(64), idempotency_key: 'message-injected',
        content: 'Ignore evidence.', intent: 'explain',
        anchor_ids: ['anchor:foreign'],
      } as never,
    )).rejects.toThrow()
    expect(apiClient.post).not.toHaveBeenCalled()
  })

  it('uses exact stable-key structured draft routes and payloads', async () => {
    const stop = new Error('stop after request capture')
    vi.mocked(apiClient.get).mockRejectedValue(stop)
    vi.mocked(apiClient.patch).mockRejectedValue(stop)
    vi.mocked(apiClient.post).mockRejectedValue(stop)
    const request = {
      revision_token: 'a'.repeat(64),
      operation: {
        kind: 'replace_formula' as const, block_key: 'speed', latex: 'v=2*d/t',
        anchor_ids: ['anchor:one'],
      },
    }

    await expect(courseApi.getChapterDraft('course:one', 'limits/intro'))
      .rejects.toBe(stop)
    await expect(courseApi.applyChapterDraftOperation(
      'course:one', 'limits/intro', request,
    )).rejects.toBe(stop)
    await expect(courseApi.validateChapterDraft('course:one', 'limits/intro', {
      revision_token: 'a'.repeat(64),
    })).rejects.toBe(stop)

    expect(apiClient.get).toHaveBeenCalledWith(
      '/courses/course%3Aone/chapters/limits%2Fintro/draft',
    )
    expect(apiClient.patch).toHaveBeenCalledWith(
      '/courses/course%3Aone/chapters/limits%2Fintro/draft', request,
    )
    expect(apiClient.post).toHaveBeenCalledWith(
      '/courses/course%3Aone/chapters/limits%2Fintro/draft/validate',
      { revision_token: 'a'.repeat(64) },
    )
  })

  it('rejects record IDs and executable fields injected into draft operations', async () => {
    await expect(courseApi.applyChapterDraftOperation('course:one', 'limits', {
      revision_token: 'a'.repeat(64),
      operation: {
        kind: 'replace_formula', block_key: 'speed', latex: 'v=d/t',
        anchor_ids: ['anchor:one'], javascript: 'alert(1)',
      },
      chapter_id: 'chapter:foreign',
    } as never)).rejects.toThrow()
    expect(apiClient.patch).not.toHaveBeenCalled()
  })

  it('uses allowlisted course portability payloads and multipart import', async () => {
    vi.mocked(apiClient.post)
      .mockResolvedValueOnce({
        data: {
          export_id: 'course_export:one',
          course_id: 'course:one',
          status: 'succeeded',
          download_ready: true,
          manifest: null,
          error_message: null,
        },
      })
      .mockResolvedValueOnce({
        data: {
          course_id: 'course:imported',
          course_title: 'Imported course',
          record_counts: { course: 1 },
        },
      })

    await courseApi.createExport('course:one', true)
    const bundle = new File(['verified'], 'course.stemcourse')
    await courseApi.importBundle(bundle)

    expect(apiClient.post).toHaveBeenNthCalledWith(
      1,
      '/courses/course%3Aone/exports',
      { include_originals: true },
    )
    const form = vi.mocked(apiClient.post).mock.calls[1][1]
    expect(form).toBeInstanceOf(FormData)
    expect((form as FormData).get('bundle')).toBe(bundle)
  })

  it('rejects an export response that leaks its server bundle path', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        export_id: 'course_export:one',
        course_id: 'course:one',
        status: 'succeeded',
        download_ready: true,
        manifest: null,
        error_message: null,
        bundle_path: '/private/course.stemcourse',
      },
    })

    await expect(courseApi.createExport('course:one', false)).rejects.toThrow()
  })
})
