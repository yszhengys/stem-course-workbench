import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ChapterPublicationGate } from './ChapterPublicationGate'
import type { CourseFinding } from '@/lib/types/course'

const defaultProps = {
  chapterStatus: 'ready',
  findings: [],
  isLoading: false,
  isError: false,
  isUpdating: false,
  isPublishing: false,
  onRetry: vi.fn(),
  onUpdate: vi.fn(),
  onPublish: vi.fn(),
}

describe('ChapterPublicationGate', () => {
  it('fails closed while validation findings are loading', () => {
    render(<ChapterPublicationGate {...defaultProps} isLoading findings={undefined} />)

    expect(screen.getByRole('status')).toHaveTextContent('course.validationLoading')
    expect(screen.getByRole('button', { name: 'course.publishChapter' })).toBeDisabled()
  })

  it('shows a retryable error and keeps publication disabled on a findings query error', () => {
    const onRetry = vi.fn()
    render(<ChapterPublicationGate {...defaultProps} isError findings={undefined} onRetry={onRetry} />)

    expect(screen.getByRole('alert')).toHaveTextContent('course.sectionLoadFailed')
    expect(screen.getByRole('button', { name: 'common.retry' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'course.publishChapter' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'common.retry' }))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('ignores cached findings and stays fail-closed during refetch and refetch errors', () => {
    const { rerender } = render(
      <ChapterPublicationGate {...defaultProps} findings={[]} isLoading />
    )
    expect(screen.getByRole('button', { name: 'course.publishChapter' })).toBeDisabled()

    rerender(<ChapterPublicationGate {...defaultProps} findings={[]} isError />)
    expect(screen.getByRole('alert')).toHaveTextContent('course.sectionLoadFailed')
    expect(screen.getByRole('button', { name: 'course.publishChapter' })).toBeDisabled()
  })

  it('blocks a terminal finding whose resolution reason is blank', () => {
    const finding = {
      id: 'course_validation_finding:one',
      course: 'course:one',
      course_version: 'course_outline_version:one',
      chapter: 'course_chapter_version:one',
      generation_run: 'course_generation_run:one',
      chapter_key: 'limits',
      finding: {
        kind: 'review',
        severity: 'high',
        item_key: 'definition-limit',
        anchor_ids: ['anchor:one'],
        status: 'resolved',
        message: 'Definition needs review.',
        reviewer_run_id: null,
        resolution_reason: '   ',
      },
      severity: 'high',
      status: 'resolved',
      resolution_reason: '   ',
      created: null,
      updated: null,
    } satisfies CourseFinding

    render(<ChapterPublicationGate {...defaultProps} findings={[finding]} />)

    expect(screen.getByText('course.publishBlocked')).toBeVisible()
    expect(screen.getByRole('button', { name: 'course.publishChapter' })).toBeDisabled()
  })

  it('allows a ready chapter to publish only after an empty findings result is known', () => {
    render(<ChapterPublicationGate {...defaultProps} />)

    expect(screen.getByText('course.noFindings')).toBeVisible()
    expect(screen.getByRole('button', { name: 'course.publishChapter' })).toBeEnabled()
  })

  it('honors an independent exercise-bank publication gate', () => {
    render(
      <ChapterPublicationGate
        {...defaultProps}
        additionalBlockedReason="course.exercisePublicationBlocked"
      />
    )

    expect(screen.getByText('course.exercisePublicationBlocked')).toBeVisible()
    expect(screen.getByRole('button', { name: 'course.publishChapter' })).toBeDisabled()
  })
})
