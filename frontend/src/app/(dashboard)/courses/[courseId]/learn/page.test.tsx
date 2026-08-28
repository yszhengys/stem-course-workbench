import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CourseLearnPage from './page'
import {
  useCourse,
  useCourseLearningOverview,
} from '@/lib/hooks/use-courses'

vi.mock('next/navigation', () => ({
  useParams: () => ({ courseId: 'course%3Aabc' }),
}))
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/lib/hooks/use-courses', () => ({
  useCourse: vi.fn(),
  useCourseLearningOverview: vi.fn(),
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

const position = {
  event_id: 'position-limits',
  course_id: 'course:abc',
  course_version_id: 'course_version:published',
  chapter_key: 'limits',
  concept_key: null,
  exercise_key: null,
  kind: 'reading_position',
  payload: { block_key: 'limits-definition' },
  occurred_at: '2026-08-22T08:00:00Z',
}

describe('CourseLearnPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useCourse).mockReturnValue(queryResult({
      id: 'course:abc',
      title: 'Calculus',
      status: 'ready',
    }) as never)
    vi.mocked(useCourseLearningOverview).mockReturnValue(queryResult({
      course_id: 'course:abc',
      course_version_id: 'course_version:published',
      chapters: [
        { chapter_key: 'limits', chapter_no: 1, title: 'Limits', latest_position: position },
        { chapter_key: 'vectors', chapter_no: 2, title: 'Vectors', latest_position: null },
      ],
      concepts: [{ key: 'limit-laws', label: 'Limit laws' }],
      masteries: [{
        course_id: 'course:abc',
        course_version_id: 'course_version:published',
        chapter_key: 'limits',
        concept_key: 'limit-laws',
        status: 'review_due',
        successful_exercise_keys: ['limits-core'],
        unrevealed_success_count: 1,
        review_level: 1,
        review_due_at: '2026-08-22T08:00:00Z',
        last_event_at: '2026-08-22T08:00:00Z',
        snapshot_hash: 'a'.repeat(64),
      }],
      review_queue: [{
        chapter_key: 'limits',
        concept_key: 'limit-laws',
        status: 'review_due',
        due_at: '2026-08-22T08:00:00Z',
        interval_days: 1,
      }],
    }) as never)
  })

  it('resumes the last exact chapter and preserves the separate Build route', () => {
    render(<CourseLearnPage />)

    expect(screen.getByRole('link', { name: 'course.continueLearning' })).toHaveAttribute(
      'href',
      '/courses/course%3Aabc/learn/limits',
    )
    expect(screen.getByRole('link', { name: 'course.openBuildMode' })).toHaveAttribute(
      'href',
      '/courses/course%3Aabc/outline',
    )
    expect(screen.getByRole('link', { name: 'Limits' })).toHaveAttribute(
      'href',
      '/courses/course%3Aabc/learn/limits',
    )
  })

  it('localizes mastery and review vocabulary instead of rendering raw enums', () => {
    render(<CourseLearnPage />)

    expect(screen.getAllByText('course.masteryReviewDue').length).toBeGreaterThan(0)
    expect(screen.getAllByText('course.reviewQueue').length).toBeGreaterThan(0)
    expect(screen.queryByText('review_due')).not.toBeInTheDocument()
    expect(screen.queryByText('limit-laws')).not.toBeInTheDocument()
    expect(screen.getAllByText('Limit laws').length).toBeGreaterThan(0)
  })
})
