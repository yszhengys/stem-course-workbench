import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CourseExercises } from './CourseExercises'

const exercise = {
  key: 'exercise-limit-1',
  prompt: 'Evaluate the limit.',
  difficulty: 'core' as const,
  hints: ['Factor first.', 'Cancel the common term.'],
  answer: '2',
  transfer_task: 'Evaluate the related limit.',
  anchor_ids: ['anchor:one'],
  oracle_expression: null,
  oracle_values: {},
  oracle_answer: null,
  provenance: 'derived',
}

describe('CourseExercises', () => {
  it('persists answer, hint, reveal, and transfer state through a stable Lab key', () => {
    const onSave = vi.fn()
    render(
      <CourseExercises
        exercises={[exercise]}
        persistentLabKey="function-plot-1"
        disabled={false}
        onSave={onSave}
      />
    )

    expect(screen.getByText('course.difficultyCore')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'course.viewHints' }))
    expect(screen.getByText('Factor first.')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'course.revealAnswer' }))
    fireEvent.change(screen.getByLabelText('course.exerciseAnswer'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'course.transferComplete' }))
    fireEvent.click(screen.getByRole('button', { name: 'course.saveExerciseAttempt' }))

    expect(onSave).toHaveBeenCalledWith({
      labKey: 'function-plot-1',
      request: {
        answers: { answer: '2' },
        exercise_key: 'exercise-limit-1',
        answer: '2',
        hints_used: 2,
        answer_revealed: true,
        transfer_completed: true,
      },
    })
  })

  it('explains that exercise state cannot be saved without a persistent Lab', () => {
    render(
      <CourseExercises exercises={[exercise]} persistentLabKey={undefined} disabled={false} onSave={vi.fn()} />
    )

    expect(screen.getByText('course.exercisePersistenceUnavailable')).toBeVisible()
    expect(screen.getByRole('button', { name: 'course.saveExerciseAttempt' })).toBeDisabled()
  })
})
