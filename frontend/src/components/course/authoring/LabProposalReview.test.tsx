import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useApproveCourseLab,
  useCourseLabs,
} from '@/lib/hooks/use-courses'
import type { CourseLab } from '@/lib/types/course'
import { LabProposalReview } from './LabProposalReview'

vi.mock('@/lib/hooks/use-courses', () => ({
  useApproveCourseLab: vi.fn(),
  useCourseLabs: vi.fn(),
}))
vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/components/course/LabRenderer', () => ({
  LabRenderer: ({ spec }: { spec: { title: string } }) => (
    <div data-testid="lab-renderer">{spec.title}</div>
  ),
}))

const proposalHash = 'a'.repeat(64)
const lab: CourseLab = {
  id: 'lab:one',
  lab_key: 'limit-plot',
  lab_type: 'function_plot',
  proposal_hash: proposalHash,
  approved_hash: null,
  approved_at: null,
  approval_reason: null,
  spec: {
    kind: 'function_plot',
    key: 'limit-plot',
    title: 'Linear plot',
    anchor_ids: [],
    provenance: 'pedagogical',
    expressions: ['a*x'],
    domain: { x: [-2, 2] },
    controls: [{ key: 'a', label: 'Slope', min: -2, max: 2, value: 1 }],
    objects: [],
    pedagogy: {
      learning_objectives: ['Relate slope to the graph.'],
      prerequisite_concepts: ['Coordinates'],
      variables: [{ key: 'a', label: 'Slope', range: [-2, 2] }],
      prediction_prompt: 'Predict the direction of change.',
      steps: ['Record a prediction.', 'Move the control.'],
      expected_observations: ['The graph changes continuously.'],
      student_submission: 'Submit one observation.',
      rubric: ['Uses graph evidence.'],
      error_boundaries: ['Stay inside the domain.'],
      accessible_alternative: 'Use the data table.',
    },
  },
}

const approve = vi.fn()

function queryResult(labs: CourseLab[] = [lab]) {
  return {
    data: labs,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }
}

describe('LabProposalReview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    approve.mockResolvedValue({ ...lab, approved_hash: proposalHash })
    vi.mocked(useCourseLabs).mockReturnValue(queryResult() as never)
    vi.mocked(useApproveCourseLab).mockReturnValue({
      mutateAsync: approve,
      isPending: false,
      error: null,
    } as never)
  })

  it('shows the complete proposal hash and requires the exact phrase plus reason', async () => {
    render(<LabProposalReview courseId="course:one" chapterKey="limits" />)

    const card = screen.getByTestId('lab-proposal-limit-plot')
    expect(within(card).getByTestId('lab-renderer')).toHaveTextContent('Linear plot')
    expect(within(card).getByText(proposalHash)).toBeVisible()
    expect(within(card).getByText('course.labApprovalStale')).toBeVisible()
    const button = within(card).getByRole('button', { name: 'course.approveLabProposal' })
    expect(button).toBeDisabled()

    fireEvent.change(within(card).getByLabelText('course.labApprovalConfirmation'), {
      target: { value: '确认实验方案' },
    })
    fireEvent.change(within(card).getByLabelText('course.labApprovalReason'), {
      target: { value: 'Checked every teaching field.' },
    })
    fireEvent.click(button)

    await waitFor(() => expect(approve).toHaveBeenCalledWith({
      labKey: 'limit-plot',
      request: {
        confirmation: '确认实验方案',
        proposal_hash: proposalHash,
        reason: 'Checked every teaching field.',
      },
    }))
  })

  it('shows immutable approval provenance without another approval action', () => {
    vi.mocked(useCourseLabs).mockReturnValue(queryResult([{
      ...lab,
      approved_hash: proposalHash,
      approved_at: '2026-08-29T02:00:00Z',
      approval_reason: 'Checked every teaching field.',
    }]) as never)

    render(<LabProposalReview courseId="course:one" chapterKey="limits" />)

    const card = screen.getByTestId('lab-proposal-limit-plot')
    expect(within(card).getByText('course.labApprovalCurrent')).toBeVisible()
    expect(within(card).getByText('Checked every teaching field.')).toBeVisible()
    expect(within(card).queryByRole('button', { name: 'course.approveLabProposal' }))
      .not.toBeInTheDocument()
  })

  it('fails closed for a legacy proposal without a canonical hash', () => {
    vi.mocked(useCourseLabs).mockReturnValue(queryResult([{
      ...lab,
      proposal_hash: null,
      spec: { ...lab.spec, pedagogy: undefined },
    }]) as never)

    render(<LabProposalReview courseId="course:one" chapterKey="limits" />)

    expect(screen.getByText('course.labApprovalLegacyBlocked')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'course.approveLabProposal' }))
      .not.toBeInTheDocument()
  })
})
