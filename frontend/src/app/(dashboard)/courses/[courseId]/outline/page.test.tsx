import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CourseOutlinePage from './page'
import { useCommandStatus } from '@/lib/hooks/use-command-status'
import {
  useApproveCourseOutline,
  useAssociateCourseSource,
  useBuildCourseEvidence,
  useCourse,
  useCourseAnchors,
  useCourseModelOptions,
  useCurrentCourseOutline,
  useEligibleCourseSources,
  useGenerateCourseOutline,
} from '@/lib/hooks/use-courses'
import type { CourseModelOptions } from '@/lib/types/course'

vi.mock('next/navigation', () => ({
  useParams: () => ({ courseId: 'course%3Aone' }),
}))
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/lib/hooks/use-command-status', () => ({ useCommandStatus: vi.fn() }))
vi.mock('@/lib/hooks/use-courses', () => ({
  useApproveCourseOutline: vi.fn(),
  useAssociateCourseSource: vi.fn(),
  useBuildCourseEvidence: vi.fn(),
  useCourse: vi.fn(),
  useCourseAnchors: vi.fn(),
  useCourseModelOptions: vi.fn(),
  useCurrentCourseOutline: vi.fn(),
  useEligibleCourseSources: vi.fn(),
  useGenerateCourseOutline: vi.fn(),
}))

const solOption = {
  adapter: 'codex_cli' as const,
  model: 'gpt-5.6-sol',
  reasoning_effort: 'max' as const,
  optional: false,
  configured: true,
  selectable: true,
}
const lunaOption = {
  ...solOption,
  model: 'gpt-5.6-luna',
}
const solSelection = {
  adapter: 'codex_cli' as const,
  model: 'gpt-5.6-sol',
  reasoning_effort: 'max' as const,
}
const lunaSelection = {
  ...solSelection,
  model: 'gpt-5.6-luna',
}

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

describe('CourseOutlinePage', () => {
  let modelData: CourseModelOptions

  beforeEach(() => {
    vi.clearAllMocks()
    modelData = {
      defaults: { outline: solSelection },
      options: [solOption],
    }

    vi.mocked(useCourse).mockReturnValue(queryResult({
      id: 'course:one',
      title: 'Calculus',
      notebook: 'notebook:course space',
      status: 'outline_ready',
      error_message: null,
      outline_version_id: null,
    }) as unknown as ReturnType<typeof useCourse>)
    vi.mocked(useEligibleCourseSources).mockReturnValue(queryResult([]) as unknown as ReturnType<typeof useEligibleCourseSources>)
    vi.mocked(useCourseAnchors).mockReturnValue(queryResult([{
      anchor_id: 'anchor:one',
      source_role: 'PRIMARY',
      locator: { kind: 'pdf_page', index: 1, quote: 'A grounded statement.' },
    }]) as unknown as ReturnType<typeof useCourseAnchors>)
    vi.mocked(useCourseModelOptions).mockImplementation(
      () => queryResult(modelData) as unknown as ReturnType<typeof useCourseModelOptions>
    )
    vi.mocked(useCurrentCourseOutline).mockReturnValue(queryResult(undefined) as unknown as ReturnType<typeof useCurrentCourseOutline>)
    vi.mocked(useAssociateCourseSource).mockReturnValue(mutationResult() as unknown as ReturnType<typeof useAssociateCourseSource>)
    vi.mocked(useBuildCourseEvidence).mockReturnValue(mutationResult() as unknown as ReturnType<typeof useBuildCourseEvidence>)
    vi.mocked(useGenerateCourseOutline).mockReturnValue(mutationResult() as unknown as ReturnType<typeof useGenerateCourseOutline>)
    vi.mocked(useApproveCourseOutline).mockReturnValue(mutationResult() as unknown as ReturnType<typeof useApproveCourseOutline>)
    vi.mocked(useCommandStatus).mockReturnValue({
      status: undefined,
      errorMessage: null,
      isTimedOut: false,
      isFetching: false,
    } as ReturnType<typeof useCommandStatus>)
  })

  it.each([
    ['is no longer selectable', [{ ...solOption, configured: false, selectable: false }, lunaOption]],
    ['is absent from the refreshed options', [lunaOption]],
  ])('clears a prior selection when it %s without falling back', async (_case, refreshedOptions) => {
    const { rerender } = render(<CourseOutlinePage />)
    const picker = screen.getByLabelText('course.modelLabel')
    const generate = screen.getByRole('button', { name: 'course.generateOutline' })

    await waitFor(() => expect(picker).toHaveValue('codex_cli|gpt-5.6-sol'))
    expect(generate).toBeEnabled()

    modelData = {
      defaults: { outline: lunaSelection },
      options: refreshedOptions,
    }
    rerender(<CourseOutlinePage />)

    await waitFor(() => expect(picker).toHaveValue(''))
    expect(generate).toBeDisabled()
  })

  it.each([
    ['is still refetching', { isFetching: true, isError: false }],
    ['failed to refetch', { isFetching: false, isError: true }],
  ])('does not render or approve a cached outline when the current-outline query %s', (_case, queryState) => {
    vi.mocked(useCourse).mockReturnValue(queryResult({
      id: 'course:one',
      title: 'Calculus',
      status: 'outline_ready',
      error_message: null,
      outline_version_id: 'course_outline_version:current',
    }) as unknown as ReturnType<typeof useCourse>)
    vi.mocked(useCurrentCourseOutline).mockReturnValue({
      ...queryResult({
        id: 'course_outline_version:stale-v1',
        version_no: 1,
        approved_at: null,
        outline_artifact: {
          title: 'Cached stale outline v1',
          chapters: [{
            key: 'limits',
            title: 'Limits',
            purpose: 'Learn limits.',
            prerequisite_keys: [],
            objective_keys: ['limit-definition'],
            anchor_ids: ['anchor:one'],
            lab_keys: [],
          }],
          concepts: [],
          dependency_edges: [],
        },
      }),
      ...queryState,
      error: queryState.isError ? new Error('refetch failed') : null,
    } as unknown as ReturnType<typeof useCurrentCourseOutline>)

    render(<CourseOutlinePage />)

    expect(screen.queryByText('Cached stale outline v1')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'course.approveOutline' })).not.toBeInTheDocument()
  })

  it('keeps the no-source CTA in the loaded Course Notebook', () => {
    render(<CourseOutlinePage />)

    expect(screen.getByRole('link', { name: 'course.goToSources' })).toHaveAttribute(
      'href',
      '/notebooks/notebook%3Acourse%20space'
    )
  })

  it('continues associating and building the selected eligible Source', async () => {
    const associate = mutationResult()
    const build = mutationResult()
    associate.mutateAsync.mockResolvedValue({})
    build.mutateAsync.mockResolvedValue({ command_id: 'command:evidence' })
    vi.mocked(useAssociateCourseSource).mockReturnValue(associate as unknown as ReturnType<typeof useAssociateCourseSource>)
    vi.mocked(useBuildCourseEvidence).mockReturnValue(build as unknown as ReturnType<typeof useBuildCourseEvidence>)
    vi.mocked(useEligibleCourseSources).mockReturnValue(queryResult([{
      source_id: 'source:pdf',
      title: 'Textbook',
      filename: 'book.pdf',
      kind: 'pdf',
      role: null,
      associated: false,
    }]) as unknown as ReturnType<typeof useEligibleCourseSources>)

    render(<CourseOutlinePage />)
    fireEvent.change(screen.getByLabelText('course.sourcePicker'), {
      target: { value: 'source:pdf' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.buildEvidence' }))

    await waitFor(() => expect(associate.mutateAsync).toHaveBeenCalledWith({
      source_id: 'source:pdf', role: 'PRIMARY',
    }))
    await waitFor(() => expect(build.mutateAsync).toHaveBeenCalledWith({
      source_id: 'source:pdf', role: 'PRIMARY', force: false,
    }))
  })
})
