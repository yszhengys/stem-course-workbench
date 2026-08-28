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
      bbox: kind === 'pptx_slide' ? [0.1, 0.2, 0.5, 0.6] : null,
    },
    quote_sha256: 'b'.repeat(64),
    source_role: 'PRIMARY',
    preview_path: kind === 'pptx_slide' ? 'private/cache/slide.svg' : null,
    visual_preview_path: kind === 'pptx_slide' ? 'private/cache/slide.png' : null,
    visual_preview_status: kind === 'pptx_slide' ? 'available' : 'text_only',
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
    const overlay = screen.getByTestId('evidence-bbox-overlay')
    const rectangle = overlay.querySelector('rect')
    expect(rectangle).toHaveAttribute('x', '0.1')
    expect(rectangle).toHaveAttribute('y', '0.2')
    expect(rectangle).toHaveAttribute('width', '0.4')
    expect(rectangle).toHaveAttribute('height', '0.39999999999999997')
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

  it('labels the honest text-only fallback without exposing cache paths', async () => {
    const fallback = anchor('pptx_slide')
    fallback.visual_preview_status = 'text_only'
    fallback.visual_preview_path = null

    render(
      <EvidenceAnchorCard
        courseId="course:one"
        anchor={fallback}
        checked={false}
        onCheckedChange={vi.fn()}
      />
    )

    expect(await screen.findByRole('img')).toBeVisible()
    expect(screen.getByText('course.textOnlyPreview')).toBeVisible()
    expect(document.body.textContent).not.toContain(String(fallback.preview_path))
  })
})
