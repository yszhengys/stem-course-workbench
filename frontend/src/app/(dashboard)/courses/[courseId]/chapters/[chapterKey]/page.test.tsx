import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CourseChapterPage from './page'
import { useCommandStatus } from '@/lib/hooks/use-command-status'
import {
  useCourse,
  useCourseAnchors,
  useCourseAttempts,
  useCourseFindings,
  useCourseLabs,
  useCourseModelOptions,
  useCourseNotes,
  useCourseProgress,
  useCreateCourseAttempt,
  useCreateCourseNote,
  useCurrentCourseChapter,
  useCurrentCourseOutline,
  useGenerateCourseChapter,
  usePublishCourseChapter,
  useReattachCourseNote,
  useReviewCourseChapter,
  useUpdateCourseFinding,
  useUpdateCourseProgress,
} from '@/lib/hooks/use-courses'

vi.mock('next/navigation', () => ({
  useParams: () => ({ courseId: 'course%3Aone', chapterKey: 'limits' }),
}))
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/components/course/ChapterPublicationGate', () => ({
  ChapterPublicationGate: () => <div />,
}))
vi.mock('@/components/course/CourseExercises', () => ({ CourseExercises: () => <div /> }))
vi.mock('@/components/course/LabRenderer', () => ({ LabRenderer: () => <div /> }))
vi.mock('@/components/ui/markdown-renderer', () => ({
  MarkdownRenderer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/lib/hooks/use-command-status', () => ({ useCommandStatus: vi.fn() }))
vi.mock('@/lib/hooks/use-courses', () => ({
  useCourse: vi.fn(),
  useCourseAnchors: vi.fn(),
  useCourseAttempts: vi.fn(),
  useCourseFindings: vi.fn(),
  useCourseLabs: vi.fn(),
  useCourseModelOptions: vi.fn(),
  useCourseNotes: vi.fn(),
  useCourseProgress: vi.fn(),
  useCreateCourseAttempt: vi.fn(),
  useCreateCourseNote: vi.fn(),
  useCurrentCourseChapter: vi.fn(),
  useCurrentCourseOutline: vi.fn(),
  useGenerateCourseChapter: vi.fn(),
  usePublishCourseChapter: vi.fn(),
  useReattachCourseNote: vi.fn(),
  useReviewCourseChapter: vi.fn(),
  useUpdateCourseFinding: vi.fn(),
  useUpdateCourseProgress: vi.fn(),
}))

function queryResult(data: unknown) {
  return {
    data,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }
}

function mutationResult() {
  return {
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
  }
}

const sol = {
  adapter: 'codex_cli' as const,
  model: 'gpt-5.6-sol',
  reasoning_effort: 'max' as const,
}
const luna = { ...sol, model: 'gpt-5.6-luna' }
const options = [sol, luna].map((selection) => ({
  ...selection,
  optional: false,
  configured: true,
  selectable: true,
}))

describe('CourseChapterPage review models', () => {
  const reviewMutation = mutationResult()

  beforeEach(() => {
    vi.clearAllMocks()
    reviewMutation.mutateAsync.mockResolvedValue({ command_id: 'command:review' })
    vi.mocked(useCourse).mockReturnValue(queryResult({
      id: 'course:one', title: 'Calculus', status: 'generating', outline_version_id: 'course_version:one',
    }) as never)
    vi.mocked(useCurrentCourseOutline).mockReturnValue(queryResult({
      id: 'course_version:one', approved_at: '2026-08-20T00:00:00Z',
      outline_artifact: {
        chapters: [{ key: 'limits', title: 'Limits', purpose: 'Learn limits.', anchor_ids: ['anchor:one'] }],
      },
    }) as never)
    vi.mocked(useCurrentCourseChapter).mockReturnValue(queryResult({
      id: 'chapter:one', status: 'reviewing', version_no: 1,
      artifact: {
        purpose: 'Learn limits.', objectives: ['Understand limits'], sections: [], formulas: [],
        worked_examples: [], labs: [], exercises: [], misconceptions: [], quick_reference: [],
      },
    }) as never)
    vi.mocked(useCourseAnchors).mockReturnValue(queryResult([{
      anchor_id: 'anchor:one', source_role: 'PRIMARY', preview_path: null,
      locator: { kind: 'pdf_page', index: 1, quote: 'Grounded.' },
    }]) as never)
    vi.mocked(useCourseModelOptions).mockReturnValue(queryResult({
      defaults: { chapter_content: sol, review: luna, escalation: sol },
      options,
    }) as never)
    for (const hook of [useCourseAttempts, useCourseFindings, useCourseLabs, useCourseNotes, useCourseProgress]) {
      vi.mocked(hook).mockReturnValue(queryResult([]) as never)
    }
    for (const hook of [
      useCreateCourseAttempt,
      useCreateCourseNote,
      useGenerateCourseChapter,
      usePublishCourseChapter,
      useReattachCourseNote,
      useUpdateCourseFinding,
      useUpdateCourseProgress,
    ]) {
      vi.mocked(hook).mockReturnValue(mutationResult() as never)
    }
    vi.mocked(useReviewCourseChapter).mockReturnValue(reviewMutation as never)
    vi.mocked(useCommandStatus).mockReturnValue({
      status: undefined, errorMessage: null, isTimedOut: false, isFetching: false,
    } as ReturnType<typeof useCommandStatus>)
  })

  it('defaults three independent stages and submits Luna plus explicit Sol', async () => {
    render(<CourseChapterPage />)

    await waitFor(() => {
      expect(document.querySelector('#course-content-model')).toHaveValue('codex_cli|gpt-5.6-sol')
      expect(document.querySelector('#course-review-model')).toHaveValue('codex_cli|gpt-5.6-luna')
      expect(document.querySelector('#course-escalation-model')).toHaveValue('codex_cli|gpt-5.6-sol')
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.reviewChapter' }))

    await waitFor(() => expect(reviewMutation.mutateAsync).toHaveBeenCalledWith({
      anchor_ids: ['anchor:one'],
      prompt_version: 'v1',
      model: luna,
      escalation_model: sol,
      force: false,
    }))
  })
})
