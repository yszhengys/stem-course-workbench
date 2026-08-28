import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { BibliographicSourceEditor } from './BibliographicSourceEditor'
import type { BibliographicSource, EligibleCourseSource } from '@/lib/types/course'

const hooks = vi.hoisted(() => ({
  useCourseBibliography: vi.fn(),
  useSaveCourseBibliography: vi.fn(),
  mutateAsync: vi.fn(),
}))

vi.mock('@/lib/hooks/use-courses', () => ({
  useCourseBibliography: hooks.useCourseBibliography,
  useSaveCourseBibliography: hooks.useSaveCourseBibliography,
}))

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const sources: EligibleCourseSource[] = [
  {
    source_id: 'source:one',
    title: 'Textbook A',
    filename: 'private-original-name.pdf',
    kind: 'pdf',
    role: 'PRIMARY',
    associated: true,
  },
  {
    source_id: 'source:two',
    title: 'Lecture Slides',
    filename: 'secret-folder/slides.pptx',
    kind: 'pptx',
    role: 'SUPPLEMENT',
    associated: true,
  },
  {
    source_id: 'source:unassociated',
    title: 'Not associated',
    filename: 'other.pdf',
    kind: 'pdf',
    role: null,
    associated: false,
  },
]

const record: BibliographicSource = {
  id: 'course_bibliographic_source:one',
  course: 'course:one',
  source: 'source:one',
  source_role: 'PRIMARY',
  authors: ['Ada Lovelace'],
  title: 'Engine Notes',
  edition: '1',
  publisher: 'Example Press',
  year: 1843,
  doi: '10.1000/engine',
  isbn: '0306406152',
  license: 'CC BY 4.0',
  manually_reviewed: false,
  created: '2026-08-29T00:00:00Z',
  updated: '2026-08-29T00:00:00Z',
}

describe('BibliographicSourceEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    hooks.useCourseBibliography.mockReturnValue({
      data: [record],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    })
    hooks.useSaveCourseBibliography.mockReturnValue({
      mutateAsync: hooks.mutateAsync,
      isPending: false,
    })
    hooks.mutateAsync.mockResolvedValue(record)
  })

  it('shows only associated sources, their roles, and no local filenames', () => {
    render(<BibliographicSourceEditor courseId="course:one" sources={sources} />)

    expect(screen.getByRole('heading', { name: 'Textbook A' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Lecture Slides' })).toBeVisible()
    expect(screen.queryByText('Not associated')).not.toBeInTheDocument()
    expect(screen.getByText('course.primary')).toBeVisible()
    expect(screen.getByText('course.supplement')).toBeVisible()
    expect(document.body.textContent).not.toContain('private-original-name.pdf')
    expect(document.body.textContent).not.toContain('secret-folder')
  })

  it('saves bounded fields with the current revision and explicit review flag', async () => {
    render(<BibliographicSourceEditor courseId="course:one" sources={sources} />)
    const section = screen.getByRole('heading', { name: 'Textbook A' }).closest('section')
    expect(section).not.toBeNull()
    const row = within(section as HTMLElement)

    fireEvent.change(row.getByLabelText('course.bibliographyAuthors'), {
      target: { value: 'Ada Lovelace\nCharles Babbage' },
    })
    fireEvent.change(row.getByLabelText('course.bibliographyTitleField'), {
      target: { value: 'Reviewed Engine Notes' },
    })
    fireEvent.click(row.getByLabelText('course.bibliographyManualReview'))
    fireEvent.click(row.getByRole('button', { name: 'course.saveBibliography' }))

    await waitFor(() => expect(hooks.mutateAsync).toHaveBeenCalledWith({
      sourceId: 'source:one',
      request: {
        expected_updated: '2026-08-29T00:00:00Z',
        authors: ['Ada Lovelace', 'Charles Babbage'],
        title: 'Reviewed Engine Notes',
        edition: '1',
        publisher: 'Example Press',
        year: 1843,
        doi: '10.1000/engine',
        isbn: '0306406152',
        license: 'CC BY 4.0',
        manually_reviewed: true,
      },
    }))
  })

  it('creates a missing record with a null expected revision', async () => {
    render(<BibliographicSourceEditor courseId="course:one" sources={sources} />)
    const section = screen.getByRole('heading', { name: 'Lecture Slides' }).closest('section')
    const row = within(section as HTMLElement)

    fireEvent.change(row.getByLabelText('course.bibliographyTitleField'), {
      target: { value: 'Vector Lecture' },
    })
    fireEvent.click(row.getByRole('button', { name: 'course.saveBibliography' }))

    await waitFor(() => expect(hooks.mutateAsync).toHaveBeenCalledWith({
      sourceId: 'source:two',
      request: expect.objectContaining({
        expected_updated: null,
        title: 'Vector Lecture',
        manually_reviewed: false,
      }),
    }))
  })
})
