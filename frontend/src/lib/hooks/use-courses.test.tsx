import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { courseApi } from '@/lib/api/course'
import { QUERY_KEYS } from '@/lib/api/query-client'
import {
  useCourseLearningChapter,
  useCourseLearningNotes,
  useCourseLearningOverview,
  useCourseLearningSources,
  useCourseChapterDraft,
  useCourseTutorSessions,
  useCreateCourseTutorSession,
  useApplyCourseChapterDraftOperation,
  useCreateCourseLearningNote,
  useSendCourseTutorMessage,
  useValidateCourseChapterDraft,
} from './use-courses'

vi.mock('@/lib/api/course', () => ({
  courseApi: {
    getLearningChapter: vi.fn(),
    getLearningNotes: vi.fn(),
    getLearningOverview: vi.fn(),
    getLearningSources: vi.fn(),
    createLearningNote: vi.fn(),
    listTutorSessions: vi.fn(),
    createTutorSession: vi.fn(),
    sendTutorMessage: vi.fn(),
    getChapterDraft: vi.fn(),
    applyChapterDraftOperation: vi.fn(),
    validateChapterDraft: vi.fn(),
  },
}))
vi.mock('@/lib/hooks/use-toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}))
vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

function wrapperFor(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

describe('Course learner queries', () => {
  let client: QueryClient

  beforeEach(() => {
    vi.clearAllMocks()
    client = new QueryClient({
      defaultOptions: { queries: { staleTime: 300_000, refetchOnWindowFocus: false } },
    })
    vi.mocked(courseApi.getLearningOverview).mockResolvedValue({} as never)
    vi.mocked(courseApi.getLearningChapter).mockResolvedValue({} as never)
    vi.mocked(courseApi.getLearningSources).mockResolvedValue({} as never)
    vi.mocked(courseApi.getLearningNotes).mockResolvedValue({} as never)
    vi.mocked(courseApi.createLearningNote).mockResolvedValue({} as never)
    vi.mocked(courseApi.listTutorSessions).mockResolvedValue([])
    vi.mocked(courseApi.createTutorSession).mockResolvedValue({} as never)
    vi.mocked(courseApi.sendTutorMessage).mockResolvedValue({} as never)
    vi.mocked(courseApi.getChapterDraft).mockResolvedValue({} as never)
    vi.mocked(courseApi.applyChapterDraftOperation).mockResolvedValue({} as never)
    vi.mocked(courseApi.validateChapterDraft).mockResolvedValue({} as never)
  })

  it('always revalidates learning overview on window focus', async () => {
    const { result } = renderHook(
      () => useCourseLearningOverview('course:one'),
      { wrapper: wrapperFor(client) },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const query = client.getQueryCache().find({
      queryKey: QUERY_KEYS.courseLearningOverview('course:one'),
    })
    const options = query?.options as {
      staleTime?: number
      refetchOnWindowFocus?: boolean
      retry?: boolean
    } | undefined
    expect(options?.staleTime).toBe(0)
    expect(options?.refetchOnWindowFocus).toBe(true)
    expect(options?.retry).toBe(false)
  })

  it('keys learner chapter data separately from the authoring artifact', async () => {
    const { result } = renderHook(
      () => useCourseLearningChapter('course:one', 'limits'),
      { wrapper: wrapperFor(client) },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(courseApi.getLearningChapter).toHaveBeenCalledWith('course:one', 'limits')
    const query = client.getQueryCache().find({
      queryKey: QUERY_KEYS.courseLearningChapter('course:one', 'limits'),
    })
    const options = query?.options as {
      staleTime?: number
      refetchOnWindowFocus?: boolean
    } | undefined
    expect(options?.staleTime).toBe(0)
    expect(options?.refetchOnWindowFocus).toBe(true)
  })

  it('keeps sources and notes inside the current learner chapter cache scope', async () => {
    const sourceHook = renderHook(
      () => useCourseLearningSources('course:one', 'limits'),
      { wrapper: wrapperFor(client) },
    )
    const noteHook = renderHook(
      () => useCourseLearningNotes('course:one', 'limits'),
      { wrapper: wrapperFor(client) },
    )
    await waitFor(() => {
      expect(sourceHook.result.current.isSuccess).toBe(true)
      expect(noteHook.result.current.isSuccess).toBe(true)
    })

    expect(courseApi.getLearningSources).toHaveBeenCalledWith('course:one', 'limits')
    expect(courseApi.getLearningNotes).toHaveBeenCalledWith('course:one', 'limits')
    const sourceOptions = client.getQueryCache().find({
      queryKey: QUERY_KEYS.courseLearningSources('course:one', 'limits'),
    })?.options as { staleTime?: number } | undefined
    const noteOptions = client.getQueryCache().find({
      queryKey: QUERY_KEYS.courseLearningNotes('course:one', 'limits'),
    })?.options as { refetchOnWindowFocus?: boolean } | undefined
    expect(sourceOptions?.staleTime).toBe(0)
    expect(noteOptions?.refetchOnWindowFocus).toBe(true)
  })

  it('creates a note with the chapter snapshot and invalidates learner notes', async () => {
    const { result } = renderHook(
      () => useCreateCourseLearningNote('course:one', 'limits'),
      { wrapper: wrapperFor(client) },
    )
    await act(async () => {
      await result.current.mutateAsync({
        snapshot_token: 'a'.repeat(64), block_key: 'definition', content: 'Remember.',
      })
    })

    expect(courseApi.createLearningNote).toHaveBeenCalledWith('course:one', 'limits', {
      snapshot_token: 'a'.repeat(64), block_key: 'definition', content: 'Remember.',
    })
  })

  it('always revalidates version-bound tutor sessions', async () => {
    const { result } = renderHook(
      () => useCourseTutorSessions('course:one'),
      { wrapper: wrapperFor(client) },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(courseApi.listTutorSessions).toHaveBeenCalledWith('course:one')
    const options = client.getQueryCache().find({
      queryKey: QUERY_KEYS.courseTutorSessions('course:one'),
    })?.options as { staleTime?: number; refetchOnWindowFocus?: boolean; retry?: boolean }
    expect(options.staleTime).toBe(0)
    expect(options.refetchOnWindowFocus).toBe(true)
    expect(options.retry).toBe(false)
  })

  it('creates a tutor session with an explicit model and refreshes its cache', async () => {
    client.setQueryData(QUERY_KEYS.courseTutorSessions('course:one'), [])
    const { result } = renderHook(
      () => useCreateCourseTutorSession('course:one'),
      { wrapper: wrapperFor(client) },
    )
    const request = {
      snapshot_token: 'a'.repeat(64), chapter_key: 'limits',
      model: { adapter: 'open_notebook' as const, model: 'model:teacher', reasoning_effort: null },
    }
    await act(async () => { await result.current.mutateAsync(request) })

    expect(courseApi.createTutorSession).toHaveBeenCalledWith('course:one', request)
    expect(client.getQueryState(QUERY_KEYS.courseTutorSessions('course:one'))?.isInvalidated)
      .toBe(true)
  })

  it('refreshes learning state after a tutor response and requires a session', async () => {
    const keys = [
      QUERY_KEYS.courseTutorSessions('course:one'),
      QUERY_KEYS.courseLearningOverview('course:one'),
      QUERY_KEYS.courseReviewQueue('course:one'),
    ]
    keys.forEach((key) => client.setQueryData(key, {}))
    const { result } = renderHook(
      () => useSendCourseTutorMessage('course:one', 'course_tutor_session:one'),
      { wrapper: wrapperFor(client) },
    )
    const request = {
      snapshot_token: 'a'.repeat(64), idempotency_key: 'message-one',
      content: 'Explain this step.', intent: 'explain' as const,
    }
    await act(async () => { await result.current.mutateAsync(request) })

    expect(courseApi.sendTutorMessage).toHaveBeenCalledWith(
      'course:one', 'course_tutor_session:one', request,
    )
    keys.forEach((key) => expect(client.getQueryState(key)?.isInvalidated).toBe(true))

    const missing = renderHook(
      () => useSendCourseTutorMessage('course:one', undefined),
      { wrapper: wrapperFor(client) },
    )
    await expect(missing.result.current.mutateAsync(request))
      .rejects.toThrow('Tutor session is required')
  })

  it('keeps a chapter draft revision in a fresh, focus-revalidated cache', async () => {
    const { result } = renderHook(
      () => useCourseChapterDraft('course:one', 'limits'),
      { wrapper: wrapperFor(client) },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(courseApi.getChapterDraft).toHaveBeenCalledWith('course:one', 'limits')
    const options = client.getQueryCache().find({
      queryKey: QUERY_KEYS.courseChapterDraft('course:one', 'limits'),
    })?.options as { staleTime?: number; refetchOnWindowFocus?: boolean; retry?: boolean }
    expect(options.staleTime).toBe(0)
    expect(options.refetchOnWindowFocus).toBe(true)
    expect(options.retry).toBe(false)
  })

  it('saves and validates the exact current draft revision', async () => {
    const draftKey = QUERY_KEYS.courseChapterDraft('course:one', 'limits')
    client.setQueryData(draftKey, {})
    const apply = renderHook(
      () => useApplyCourseChapterDraftOperation('course:one', 'limits'),
      { wrapper: wrapperFor(client) },
    )
    const request = {
      revision_token: 'a'.repeat(64),
      operation: {
        kind: 'replace_text' as const, block_key: 'definition',
        text: 'Updated.', anchor_ids: ['anchor:one'],
      },
    }
    await act(async () => { await apply.result.current.mutateAsync(request) })
    expect(courseApi.applyChapterDraftOperation)
      .toHaveBeenCalledWith('course:one', 'limits', request)
    expect(client.getQueryState(draftKey)?.isInvalidated).toBe(true)

    client.setQueryData(draftKey, {})
    const validate = renderHook(
      () => useValidateCourseChapterDraft('course:one', 'limits'),
      { wrapper: wrapperFor(client) },
    )
    await act(async () => {
      await validate.result.current.mutateAsync({ revision_token: 'b'.repeat(64) })
    })
    expect(courseApi.validateChapterDraft).toHaveBeenCalledWith(
      'course:one', 'limits', { revision_token: 'b'.repeat(64) },
    )
    expect(client.getQueryState(draftKey)?.isInvalidated).toBe(true)
  })
})
