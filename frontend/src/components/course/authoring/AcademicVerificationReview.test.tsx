import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useCourseChapterDraft,
  useVerifyCourseAcademicArtifact,
} from '@/lib/hooks/use-courses'
import type { CourseDraft } from '@/lib/types/course'
import { AcademicVerificationReview } from './AcademicVerificationReview'

vi.mock('@/lib/hooks/use-courses', () => ({
  useCourseChapterDraft: vi.fn(),
  useVerifyCourseAcademicArtifact: vi.fn(),
}))
vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const verify = vi.fn()
const l1 = {
  level: 'L1' as const,
  method: 'self_consistency' as const,
  anchor_ids: [],
  reason: null,
  verified_at: null,
  artifact_hash: null,
}

const draft: CourseDraft = {
  chapter_key: 'limits',
  chapter_status: 'reviewing',
  editable: true,
  revision_no: 3,
  revision_token: 'a'.repeat(64),
  revision_status: 'validated',
  artifact_hash: 'b'.repeat(64),
  artifact: {
    chapter_key: 'limits',
    purpose: 'Understand limits.',
    prerequisites: [],
    objectives: ['Evaluate limits.'],
    sections: [{
      key: 'definition', title: 'Definition', markdown: 'A limit is approached.',
      anchor_ids: ['anchor:one'], provenance: 'adapted',
    }],
    definitions: [],
    formulas: [{
      key: 'limit-law', latex: 'x+0', meaning: 'Identity',
      anchor_ids: ['anchor:one'], unit_expression: null,
      oracle_unit_expression: null, provenance: 'adapted',
      oracle_expression: null, oracle_substitutions: {}, verification: l1,
    }],
    worked_examples: [{
      key: 'worked-one', prompt: 'Compute.', steps: ['Add.'], answer: '4',
      anchor_ids: ['anchor:one'], oracle_expression: null, oracle_values: {},
      oracle_answer: null, unit_expression: null, oracle_unit_expression: null,
      provenance: 'adapted', verification: l1,
    }],
    labs: [],
    misconceptions: [],
    pitfalls: [],
    exercises: [{
      key: 'legacy-one', prompt: 'Compute.', difficulty: 'core', hints: [],
      answer: '6', transfer_task: 'Transfer.', anchor_ids: ['anchor:one'],
      oracle_expression: null, oracle_values: {}, oracle_answer: null,
      provenance: 'adapted', verification: l1,
    }],
    quick_reference: [],
    citations: ['anchor:one'],
    attributions: {
      purpose: { anchor_ids: ['anchor:one'], provenance: 'adapted' },
      prerequisites: [],
      objectives: [{ anchor_ids: ['anchor:one'], provenance: 'adapted' }],
      definitions: [], misconceptions: [], pitfalls: [], quick_reference: [],
    },
    physics_checks: [],
  },
  exercises: [],
}

function queryResult(value: CourseDraft = draft) {
  return {
    data: value,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }
}

describe('AcademicVerificationReview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    verify.mockResolvedValue(draft)
    vi.mocked(useCourseChapterDraft).mockReturnValue(queryResult() as never)
    vi.mocked(useVerifyCourseAcademicArtifact).mockReturnValue({
      mutateAsync: verify,
      isPending: false,
      error: null,
    } as never)
  })

  it('shows every answer-bearing artifact with an honest L1 label and source details', () => {
    render(<AcademicVerificationReview courseId="course:one" chapterKey="limits" />)

    expect(screen.getByTestId('academic-verification-formula-limit-law')).toHaveTextContent('L1')
    expect(screen.getByTestId('academic-verification-worked_example-worked-one')).toHaveTextContent('self_consistency')
    expect(screen.getByTestId('academic-verification-legacy_exercise-legacy-one')).toHaveTextContent('L1')
    expect(screen.queryByText(/proved correct/i)).not.toBeInTheDocument()
  })

  it('requires the exact displayed value, reason, and anchors before requesting L3', async () => {
    render(<AcademicVerificationReview courseId="course:one" chapterKey="limits" />)
    const card = screen.getByTestId('academic-verification-formula-limit-law')
    const button = within(card).getByRole('button', { name: /course.academicVerifyHuman.*limit-law/ })

    expect(button).toBeDisabled()
    fireEvent.change(within(card).getByLabelText(/course.academicExactConfirmation/), {
      target: { value: 'x+0' },
    })
    fireEvent.change(within(card).getByLabelText(/course.verificationReason/), {
      target: { value: 'Checked against the cited source.' },
    })
    fireEvent.change(within(card).getByLabelText(/course.evidenceAnchors/), {
      target: { value: 'anchor:one' },
    })
    fireEvent.click(button)

    await waitFor(() => expect(verify).toHaveBeenCalledWith({
      targetKind: 'formula',
      targetKey: 'limit-law',
      request: {
        revision_token: 'a'.repeat(64),
        exact_value_confirmation: 'x+0',
        reason: 'Checked against the cited source.',
        anchor_ids: ['anchor:one'],
      },
    }))
  })

  it('disables verification for a read-only chapter', () => {
    vi.mocked(useCourseChapterDraft).mockReturnValue(queryResult({
      ...draft,
      editable: false,
      chapter_status: 'published',
    }) as never)

    render(<AcademicVerificationReview courseId="course:one" chapterKey="limits" />)

    expect(screen.getAllByRole('button', { name: /course.academicVerifyHuman/ }))
      .toEqual(expect.arrayContaining([
        expect.objectContaining({ disabled: true }),
      ]))
  })
})
