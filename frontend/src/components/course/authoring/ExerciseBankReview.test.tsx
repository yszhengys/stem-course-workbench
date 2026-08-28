import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ExerciseBankReview } from './ExerciseBankReview'

const sol = {
  adapter: 'codex_cli' as const,
  model: 'gpt-5.6-sol',
  reasoning_effort: 'max' as const,
}
const luna = { ...sol, model: 'gpt-5.6-luna' }
const options = [sol, luna].map((selection) => ({
  ...selection,
  optional: false,
  configured: true,
  selectable: true,
}))

const status = {
  run_id: 'course_generation_run:one',
  command_id: 'command:one',
  status: 'succeeded' as const,
  error_message: null,
  exercise_count: 1,
  exercises: [{
    key: 'limits-core',
    snapshot_token: 'a'.repeat(64),
    expected_answer: '4',
    verification: {
      level: 'L1' as const,
      method: 'independent_model_review' as const,
      anchor_ids: [],
      reason: null,
      verified_at: null,
    },
    review_run_ids: ['course_generation_run:review'],
    blueprint: {
      key: 'limits-core',
      chapter_key: 'limits',
      prompt: 'Evaluate the source-grounded limit.',
      concept_keys: ['limit-laws'],
      exercise_type: 'generated_core' as const,
      answer_type: 'numeric' as const,
      hints: ['Identify the invariant.', 'Represent it.', 'Solve it.', 'Check it.'],
      source_anchor_ids: ['anchor:limits'],
      source_number: null,
      source_section: null,
      difficulty: {
        concept_count: 1,
        reasoning_steps: 2,
        symbolic_depth: 1,
        representation_shifts: 0,
        proof_burden: 0,
        physics_constraints: 0,
      },
      grader: { kind: 'numeric' as const, expected: '4', absolute_tolerance: 0, relative_tolerance: 0 },
      is_core: true,
      is_gating: true,
      is_source_level: false,
      transfer_task: {
        key: 'limits-transfer',
        prompt: 'Apply the invariant to a graph.',
        invariant_concept_keys: ['limit-laws'],
        dimensions: ['representation' as const],
        change_evidence: [],
        answer_type: 'numeric' as const,
        difficulty: {
          concept_count: 1,
          reasoning_steps: 2,
          symbolic_depth: 1,
          representation_shifts: 1,
          proof_burden: 0,
          physics_constraints: 0,
        },
        grader: { kind: 'numeric' as const, expected: '8', absolute_tolerance: 0, relative_tolerance: 0 },
        anchor_ids: ['anchor:limits'],
      },
    },
  }],
}

const anchors = [{
  id: 'course_evidence_anchor:limits',
  course: 'course:one',
  source: 'source:one',
  evidence: 'course_evidence:one',
  anchor_id: 'anchor:limits',
  quote_sha256: 'c'.repeat(64),
  source_role: 'PRIMARY' as const,
  preview_path: null,
  is_current: true,
  locator: {
    source_id: 'source:one',
    kind: 'pdf_page' as const,
    index: 3,
    block_key: 'exercise-3',
    quote: 'The source states the limit exercise and answer context.',
    content_sha256: 'b'.repeat(64),
    bbox: null,
  },
}]

function renderReview(overrides: Record<string, unknown> = {}) {
  const onGenerate = vi.fn()
  const onVerify = vi.fn()
  render(
    <ExerciseBankReview
      status={status}
      anchors={anchors}
      findings={[]}
      options={options}
      generationModel={sol}
      reviewModel={luna}
      onGenerationModelChange={vi.fn()}
      onReviewModelChange={vi.fn()}
      canGenerate
      isGenerating={false}
      isVerifying={false}
      onGenerate={onGenerate}
      onVerify={onVerify}
      onRetry={vi.fn()}
      {...overrides}
    />,
  )
  return { onGenerate, onVerify }
}

describe('ExerciseBankReview', () => {
  it('shows the reviewed grader, evidence, transfer task and honest L1 badge', () => {
    renderReview()

    expect(screen.getByText('Evaluate the source-grounded limit.')).toBeVisible()
    expect(screen.getByText('"4"')).toBeVisible()
    expect(screen.getByText(/source states the limit exercise/)).toBeVisible()
    expect(screen.getByText('Apply the invariant to a graph.')).toBeVisible()
    expect(screen.getByText('course.verificationL1')).toBeVisible()
    expect(screen.getByText('course.exerciseReviewRecorded')).toBeVisible()
  })

  it('binds human confirmation to the exact snapshot and answer without sending a grader', async () => {
    const { onVerify } = renderReview()

    fireEvent.click(screen.getByRole('checkbox', { name: 'course.confirmExpectedAnswer' }))
    fireEvent.change(screen.getByLabelText('course.verificationReason'), {
      target: { value: 'I independently checked the derivation and displayed answer.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.verifyExercise' }))

    await waitFor(() => expect(onVerify).toHaveBeenCalledWith('limits-core', {
      snapshot_token: 'a'.repeat(64),
      expected_answer_confirmation: '4',
      reason: 'I independently checked the derivation and displayed answer.',
    }))
    expect(onVerify.mock.calls[0][1]).not.toHaveProperty('grader')
  })

  it('shows terminal backend errors and never changes either explicit model', () => {
    const generationChange = vi.fn()
    const reviewChange = vi.fn()
    renderReview({
      status: {
        ...status,
        status: 'failed',
        error_message: 'Independent review found a shallow transfer task.',
        exercise_count: 0,
        exercises: [],
      },
      onGenerationModelChange: generationChange,
      onReviewModelChange: reviewChange,
    })

    expect(screen.getByText('Independent review found a shallow transfer task.')).toBeVisible()
    expect(generationChange).not.toHaveBeenCalled()
    expect(reviewChange).not.toHaveBeenCalled()
  })

  it.each(['queued', 'running'] as const)(
    'shows the real %s state and prevents duplicate generation',
    (jobStatus) => {
      const { onGenerate } = renderReview({
        status: {
          ...status,
          status: jobStatus,
          exercise_count: 0,
          exercises: [],
        },
        isGenerating: true,
      })

      expect(screen.getByRole('alert')).toHaveTextContent('course.jobStatus')
      expect(screen.getByRole('alert')).toHaveTextContent('course.jobRunningDescription')
      expect(screen.getByRole('button', { name: 'course.generateExerciseBank' })).toBeDisabled()
      expect(onGenerate).not.toHaveBeenCalled()
    },
  )
})
