import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CourseSourcePicker } from './CourseSourcePicker'
import { notebooksApi } from '@/lib/api/notebooks'
import { sourcesApi } from '@/lib/api/sources'

describe('CourseSourcePicker', () => {
  afterEach(() => vi.restoreAllMocks())

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

  it('links an existing PDF from the empty Course picker and selects it', async () => {
    const onSourceIdChange = vi.fn()
    const onSourcesChanged = vi.fn()
    vi.spyOn(sourcesApi, 'list').mockImplementation(async (params) => {
      if (params?.notebook_id) return []
      return [
        {
          id: 'source:pdf',
          title: 'Rudin textbook',
          asset: { file_path: '/private/materials/rudin.pdf' },
          embedded: false,
          embedded_chunks: 0,
          insights_count: 0,
          created: '2026-08-20T00:00:00Z',
          updated: '2026-08-20T00:00:00Z',
        },
        {
          id: 'source:url',
          title: 'Website source',
          asset: { url: 'https://example.test' },
          embedded: false,
          embedded_chunks: 0,
          insights_count: 0,
          created: '2026-08-20T00:00:00Z',
          updated: '2026-08-20T00:00:00Z',
        },
      ]
    })
    vi.spyOn(notebooksApi, 'addSource').mockResolvedValue({
      message: 'Source linked to notebook successfully',
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <CourseSourcePicker
          sources={[]}
          notebookId="notebook:course"
          sourceId=""
          role="PRIMARY"
          onSourceIdChange={onSourceIdChange}
          onRoleChange={vi.fn()}
          onSourcesChanged={onSourcesChanged}
        />
      </QueryClientProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: 'sources.addExistingTitle' }))
    expect(await screen.findByText('Rudin textbook')).toBeVisible()
    expect(screen.queryByText('Website source')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'common.addSelected' }))

    await waitFor(() => expect(notebooksApi.addSource).toHaveBeenCalledWith(
      'notebook:course',
      'source:pdf'
    ))
    await waitFor(() => expect(onSourceIdChange).toHaveBeenCalledWith('source:pdf'))
    expect(onSourcesChanged).toHaveBeenCalledOnce()
  })
})
