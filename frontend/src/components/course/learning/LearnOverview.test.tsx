import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { LearnOverview } from './LearnOverview'
import type { Course, CourseLearningOverview } from '@/lib/types/course'

vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({
    language: 'en',
    t: (key: string) => key,
  }),
}))

describe('LearnOverview', () => {
  it('uses approved concept labels and never exposes stable concept keys', () => {
    const course = {
      id: 'course:one', title: 'Calculus', status: 'ready',
    } as Course
    const overview = {
      course_id: 'course:one',
      course_version_id: 'course_version:one',
      chapters: [{
        chapter_key: 'limits', chapter_no: 1, title: 'Limits',
        snapshot_token: 'a'.repeat(64), latest_position: null,
      }],
      concepts: [{ key: 'limit-laws-internal', label: 'Limit laws' }],
      masteries: [{
        course_id: 'course:one', course_version_id: 'course_version:one',
        chapter_key: 'limits', concept_key: 'limit-laws-internal', status: 'review_due',
        successful_exercise_keys: [], unrevealed_success_count: 0,
        review_level: 1, review_due_at: '2026-08-23T12:00:00Z',
        last_event_at: '2026-08-22T12:00:00Z', snapshot_hash: 'b'.repeat(64),
      }],
      review_queue: [{
        chapter_key: 'limits', concept_key: 'limit-laws-internal', status: 'review_due',
        due_at: '2026-08-23T12:00:00Z', interval_days: 1,
      }],
    } satisfies CourseLearningOverview

    render(<LearnOverview course={course} overview={overview} />)

    expect(screen.getByRole('link', { name: 'Limit laws' })).toBeVisible()
    expect(screen.getByText(/^Limit laws:/)).toBeVisible()
    expect(screen.queryByText(/Limit laws internal/)).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain('limit-laws-internal')
  })

  it('uses a neutral label when an old event has no approved concept label', () => {
    const course = { id: 'course:one', title: 'Calculus', status: 'ready' } as Course
    const overview = {
      course_id: 'course:one', course_version_id: 'course_version:one',
      chapters: [{
        chapter_key: 'limits', chapter_no: 1, title: 'Limits',
        snapshot_token: 'a'.repeat(64), latest_position: null,
      }],
      concepts: [],
      masteries: [{
        course_id: 'course:one', course_version_id: 'course_version:one',
        chapter_key: 'limits', concept_key: 'legacy-secret-key', status: 'learning',
        successful_exercise_keys: [], unrevealed_success_count: 0,
        review_level: 0, review_due_at: null, last_event_at: null,
        snapshot_hash: 'b'.repeat(64),
      }],
      review_queue: [],
    } satisfies CourseLearningOverview

    render(<LearnOverview course={course} overview={overview} />)

    expect(screen.getByText(/course.conceptLabelUnavailable/)).toBeVisible()
    expect(document.body.textContent).not.toContain('legacy-secret-key')
  })
})
