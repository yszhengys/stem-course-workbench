import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CourseSourcePicker } from './CourseSourcePicker'

describe('CourseSourcePicker', () => {
  const sources = [
    {
      source_id: 'source:pdf', title: 'Textbook', filename: 'book.pdf', kind: 'pdf' as const,
      role: null, associated: false,
    },
    {
      source_id: 'source:pptx', title: 'Slides', filename: 'slides.pptx', kind: 'pptx' as const,
      role: 'SUPPLEMENT' as const, associated: true,
    },
  ]

  it('shows PDF and PPTX choices and fills the stable Source ID', () => {
    const onSourceIdChange = vi.fn()
    render(
      <CourseSourcePicker
        sources={sources}
        notebookId="notebook:course space"
        sourceId=""
        role="PRIMARY"
        onSourceIdChange={onSourceIdChange}
        onRoleChange={vi.fn()}
      />
    )

    expect(screen.getByText('book.pdf')).toBeVisible()
    expect(screen.getByText('slides.pptx')).toBeVisible()
    fireEvent.change(screen.getByLabelText('course.sourcePicker'), {
      target: { value: 'source:pdf' },
    })
    expect(onSourceIdChange).toHaveBeenCalledWith('source:pdf')
  })

  it('keeps a manual Source ID fallback and a usable no-source action', () => {
    const onSourceIdChange = vi.fn()
    const { rerender } = render(
      <CourseSourcePicker
        sources={sources}
        notebookId="notebook:course space"
        sourceId=""
        role="PRIMARY"
        onSourceIdChange={onSourceIdChange}
        onRoleChange={vi.fn()}
      />
    )

    fireEvent.change(screen.getByLabelText('course.manualSourceId'), {
      target: { value: 'source:manual' },
    })
    expect(onSourceIdChange).toHaveBeenCalledWith('source:manual')

    rerender(
      <CourseSourcePicker
        sources={[]}
        notebookId="notebook:course space"
        sourceId=""
        role="PRIMARY"
        onSourceIdChange={onSourceIdChange}
        onRoleChange={vi.fn()}
      />
    )
    expect(screen.getByRole('link', { name: 'course.goToSources' })).toHaveAttribute(
      'href',
      '/notebooks/notebook%3Acourse%20space'
    )
  })
})
