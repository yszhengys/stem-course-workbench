import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { LearnerSources } from './LearnerSources'
import type { CourseLearnerSourcesResponse } from '@/lib/types/course'

const api = vi.hoisted(() => ({
  getEvidencePreviewBlob: vi.fn(),
  getEvidenceSourceBlob: vi.fn(),
}))

vi.mock('@/lib/api/course', () => ({ courseApi: api }))
vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values ? `${key}:${Object.values(values).join(':')}` : key,
  }),
}))

const response: CourseLearnerSourcesResponse = {
  snapshot_token: 'a'.repeat(64),
  sources: [
    {
      anchor_id: 'private-anchor-pdf',
      filename: 'Algebra.pdf',
      kind: 'pdf_page',
      index: 7,
      quote: 'A polynomial identity from the source.',
      source_role: 'PRIMARY',
      bbox: null,
    },
    {
      anchor_id: 'private-anchor-slide',
      filename: 'Vectors.pptx',
      kind: 'pptx_slide',
      index: 2,
      quote: 'A vector diagram from the source.',
      source_role: 'SUPPLEMENT',
      bbox: [0.1, 0.2, 0.7, 0.8],
    },
  ],
}

describe('LearnerSources', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getEvidencePreviewBlob.mockResolvedValue(new Blob(['preview'], { type: 'image/svg+xml' }))
    api.getEvidenceSourceBlob.mockResolvedValue(new Blob(['source']))
    vi.stubGlobal('open', vi.fn())
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:learner-source')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  })

  it('shows readable source labels and opens a PDF at the cited page', async () => {
    let releaseSource: ((blob: Blob) => void) | undefined
    api.getEvidenceSourceBlob.mockReturnValueOnce(new Promise<Blob>((resolve) => {
      releaseSource = resolve
    }))
    const popup = {
      opener: window,
      location: { href: 'about:blank' },
      close: vi.fn(),
    }
    vi.mocked(window.open).mockReturnValue(popup as unknown as Window)
    render(<LearnerSources courseId="course:one" response={response} />)

    expect(screen.getByText('Algebra.pdf')).toBeInTheDocument()
    expect(screen.getByText('A polynomial identity from the source.')).toBeInTheDocument()
    expect(screen.getByText('private-anchor-pdf')).toBeInTheDocument()
    expect(screen.getByText('private-anchor-pdf').closest('article')).toHaveAttribute(
      'id', 'course-source-private-anchor-pdf',
    )
    fireEvent.click(screen.getByRole('button', { name: 'course.openSourcePage:7' }))

    expect(window.open).toHaveBeenCalledWith('about:blank', '_blank')
    expect(popup.opener).toBeNull()
    expect(popup.location.href).toBe('about:blank')
    releaseSource?.(new Blob(['source']))

    await waitFor(() => expect(api.getEvidenceSourceBlob).toHaveBeenCalledWith(
      'course:one', 'private-anchor-pdf',
    ))
    await waitFor(() => expect(popup.location.href).toBe('blob:learner-source#page=7'))
  })

  it('loads an authenticated slide preview and keeps the original download action', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    render(<LearnerSources courseId="course:one" response={response} />)

    expect(await screen.findByRole('img', { name: 'course.slidePreview:2' })).toHaveAttribute(
      'src', 'blob:learner-source',
    )
    expect(api.getEvidencePreviewBlob).toHaveBeenCalledWith(
      'course:one', 'private-anchor-slide',
    )
    fireEvent.click(screen.getByRole('button', { name: 'course.downloadOriginal' }))
    await waitFor(() => expect(api.getEvidenceSourceBlob).toHaveBeenCalledWith(
      'course:one', 'private-anchor-slide',
    ))
    expect(click).toHaveBeenCalledOnce()
  })
})
