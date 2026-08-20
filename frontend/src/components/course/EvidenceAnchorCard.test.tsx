import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { EvidenceAnchorCard } from './EvidenceAnchorCard'
import type { EvidenceAnchor } from '@/lib/types/course'

const api = vi.hoisted(() => ({
  getEvidencePreviewBlob: vi.fn(),
  getEvidenceSourceBlob: vi.fn(),
}))

vi.mock('@/lib/api/course', () => ({ courseApi: api }))

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { page?: number; slide?: number }) =>
      values ? `${key}:${values.page ?? values.slide}` : key,
  }),
}))

function anchor(kind: 'pdf_page' | 'pptx_slide'): EvidenceAnchor {
  return {
    id: `course_evidence_anchor:${kind}`,
    course: 'course:one',
    source: 'source:one',
    evidence: 'evidence:one',
    anchor_id: `anchor:${kind}`,
    locator: {
      source_id: 'source:one',
      kind,
      index: kind === 'pdf_page' ? 7 : 2,
      block_key: '#/texts/0',
      quote: 'Grounded quote.',
      content_sha256: 'a'.repeat(64),
      bbox: null,
    },
    quote_sha256: 'b'.repeat(64),
    source_role: 'PRIMARY',
    preview_path: kind === 'pptx_slide' ? 'private/cache/slide.svg' : null,
    is_current: true,
  }
}

describe('EvidenceAnchorCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getEvidencePreviewBlob.mockResolvedValue(new Blob(['preview'], { type: 'image/svg+xml' }))
    api.getEvidenceSourceBlob.mockResolvedValue(new Blob(['source']))
    vi.stubGlobal('open', vi.fn())
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:course-evidence')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  })

  it('opens an authenticated PDF Blob at its 1-based page', async () => {
    render(
      <EvidenceAnchorCard
        courseId="course:one"
        anchor={anchor('pdf_page')}
        checked
        onCheckedChange={vi.fn()}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'course.openSourcePage:7' }))
    await waitFor(() => expect(api.getEvidenceSourceBlob).toHaveBeenCalledWith(
      'course:one', 'anchor:pdf_page'
    ))
    expect(window.open).toHaveBeenCalledWith(
      'blob:course-evidence#page=7', '_blank', 'noopener,noreferrer'
    )
    expect(api.getEvidencePreviewBlob).not.toHaveBeenCalled()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('renders an authenticated PPTX Blob and keeps an original download action', async () => {
    const pptx = anchor('pptx_slide')
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    render(
      <EvidenceAnchorCard
        courseId="course:one"
        anchor={pptx}
        checked={false}
        onCheckedChange={vi.fn()}
      />
    )

    expect(await screen.findByRole('img', { name: 'course.slidePreview:2' })).toHaveAttribute(
      'src', 'blob:course-evidence'
    )
    expect(api.getEvidencePreviewBlob).toHaveBeenCalledWith('course:one', 'anchor:pptx_slide')
    fireEvent.click(screen.getByRole('button', { name: 'course.downloadOriginal' }))
    await waitFor(() => expect(api.getEvidenceSourceBlob).toHaveBeenCalledWith(
      'course:one', 'anchor:pptx_slide'
    ))
    expect(click).toHaveBeenCalledOnce()
    expect(document.body.textContent).not.toContain(String(pptx.preview_path))
    expect(document.body.innerHTML).not.toContain(String(pptx.preview_path))
  })

  it('fails visibly when a stored PPTX preview is absent or cannot load', async () => {
    const missing = anchor('pptx_slide')
    missing.preview_path = null
    const { rerender } = render(
      <EvidenceAnchorCard
        courseId="course:one"
        anchor={missing}
        checked={false}
        onCheckedChange={vi.fn()}
      />
    )
    expect(screen.getByText('course.previewUnavailable')).toBeInTheDocument()

    const available = anchor('pptx_slide')
    api.getEvidencePreviewBlob.mockRejectedValueOnce(new Error('401'))
    rerender(
      <EvidenceAnchorCard
        courseId="course:one"
        anchor={available}
        checked={false}
        onCheckedChange={vi.fn()}
      />
    )
    expect(await screen.findByText('course.sourcePreviewFailed')).toBeInTheDocument()
  })
})
