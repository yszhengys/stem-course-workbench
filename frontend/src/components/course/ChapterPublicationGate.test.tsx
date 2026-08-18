import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ChapterPublicationGate } from './ChapterPublicationGate'

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

  it('allows a ready chapter to publish only after an empty findings result is known', () => {
    render(<ChapterPublicationGate {...defaultProps} />)

    expect(screen.getByText('course.noFindings')).toBeVisible()
    expect(screen.getByRole('button', { name: 'course.publishChapter' })).toBeEnabled()
  })
})
