import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChapterNotes } from './ChapterNotes'
import type {
  CourseLearnerChapterArtifact,
  CourseLearnerNotesResponse,
} from '@/lib/types/course'

const mutateAsync = vi.hoisted(() => vi.fn())
vi.mock('@/lib/hooks/use-courses', () => ({
  useCreateCourseLearningNote: () => ({ mutateAsync, isPending: false }),
}))
vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const artifact = {
  purpose: 'Purpose',
  prerequisites: [],
  objectives: ['Understand limits'],
  sections: [
    {
      block_key: 'limits-definition',
      title: 'Definition of a limit',
      markdown: 'A limit describes approach.',
      anchor_ids: [],
      provenance: 'adapted',
    },
  ],
  definitions: [],
  formulas: [],
  worked_examples: [],
  misconceptions: [],
  pitfalls: [],
  quick_reference: [],
  citations: [],
} satisfies CourseLearnerChapterArtifact

const notes: CourseLearnerNotesResponse = {
  snapshot_token: 'a'.repeat(64),
  notes: [{
    note_id: 'course_note:one',
    block_key: 'limits-definition',
    content: 'Approach is not equality.',
    orphan_status: 'active',
    created: '2026-08-23T12:00:00+00:00',
  }],
}

describe('ChapterNotes', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mutateAsync.mockResolvedValue({})
  })

  it('labels notes by current chapter content instead of internal record identifiers', () => {
    render(
      <ChapterNotes
        courseId="course:one"
        chapterKey="limits"
        snapshotToken={'a'.repeat(64)}
        artifact={artifact}
        response={notes}
      />,
    )

    expect(screen.getAllByText('Definition of a limit')).toHaveLength(2)
    expect(screen.getByText('Approach is not equality.')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('course_note:one')
    expect(document.body.textContent).not.toContain('limits-definition')
  })

  it('creates a note for a stable block with the exact current snapshot', async () => {
    render(
      <ChapterNotes
        courseId="course:one"
        chapterKey="limits"
        snapshotToken={'b'.repeat(64)}
        artifact={artifact}
        response={{ ...notes, snapshot_token: 'b'.repeat(64), notes: [] }}
      />,
    )

    fireEvent.change(screen.getByLabelText('course.selectBlock'), {
      target: { value: 'limits-definition' },
    })
    fireEvent.change(screen.getByLabelText('course.notePlaceholder'), {
      target: { value: 'Remember the epsilon definition.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.saveNote' }))

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith({
      snapshot_token: 'b'.repeat(64),
      block_key: 'limits-definition',
      content: 'Remember the epsilon definition.',
    }))
  })
})
