import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CoverageReport } from './CoverageReport'
import type { CourseCoverageReport } from '@/lib/types/course'

const mocks = vi.hoisted(() => ({
  useCourseCoverage: vi.fn(),
  downloadCoverage: vi.fn(),
}))

vi.mock('@/lib/hooks/use-courses', () => ({
  useCourseCoverage: mocks.useCourseCoverage,
}))

vi.mock('@/lib/api/course', () => ({
  courseApi: { downloadCoverage: mocks.downloadCoverage },
}))

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const report: CourseCoverageReport = {
  schema_version: 1,
  course_id: 'course:one',
  course_version_id: 'course_version:current',
  source_hashes: [
    { source_id: 'source:one', content_sha256: '1'.repeat(64) },
    { source_id: 'source:two', content_sha256: '2'.repeat(64) },
  ],
  rows: [
    {
      anchor_id: 'anchor:used',
      source_id: 'source:one',
      source_role: 'PRIMARY',
      locator: {
        kind: 'pdf_page',
        index: 2,
        block_key: 'Definition 1',
        content_sha256: '1'.repeat(64),
        bbox: [0.1, 0.2, 0.8, 0.9],
      },
      category: 'definition',
      confidence: 'high',
      source_number: '1',
      usages: [{ kind: 'concept', key: 'vectors', chapter_key: null }],
      flags: [],
    },
    {
      anchor_id: 'anchor:unused',
      source_id: 'source:two',
      source_role: 'SUPPLEMENT',
      locator: {
        kind: 'pptx_slide',
        index: 5,
        block_key: 'Visual block',
        content_sha256: '2'.repeat(64),
        bbox: null,
      },
      category: 'unclassified',
      confidence: 'low',
      source_number: null,
      usages: [],
      flags: ['unused', 'low_confidence', 'missing_bibliography'],
    },
  ],
  chapter_flags: [{ chapter_key: 'chapter-b', flags: ['no_answer_source'] }],
  flags: ['generation_limit_exceeded'],
  report_hash: 'a'.repeat(64),
}

describe('CoverageReport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.useCourseCoverage.mockReturnValue({
      data: report,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    })
    mocks.downloadCoverage.mockResolvedValue(undefined)
  })

  it('shows portable mappings and audit flags without private source content', () => {
    render(<CoverageReport courseId="course:one" enabled />)

    expect(screen.getByText('source:one')).toBeVisible()
    expect(screen.getByText('Definition 1')).toBeVisible()
    expect(screen.getByText('vectors')).toBeVisible()
    expect(screen.getByText('course.coverageGenerationLimit')).toBeVisible()
    expect(screen.getByText('chapter-b')).toBeVisible()
    expect(screen.getByText('a'.repeat(64))).toBeVisible()
    expect(document.body.textContent).not.toContain('A bounded source quote')
    expect(document.body.textContent).not.toContain('/Users/')
  })

  it('filters rows by deterministic flags', () => {
    render(<CoverageReport courseId="course:one" enabled />)

    fireEvent.click(screen.getByLabelText('course.coverageFilterUnused'))

    expect(screen.queryByText('Definition 1')).not.toBeInTheDocument()
    expect(screen.getByText('Visual block')).toBeVisible()
  })

  it('downloads the server-generated JSON attachment', async () => {
    render(<CoverageReport courseId="course:one" enabled />)

    fireEvent.click(screen.getByRole('button', { name: 'course.coverageDownload' }))

    await waitFor(() => {
      expect(mocks.downloadCoverage).toHaveBeenCalledWith('course:one')
    })
  })
})
