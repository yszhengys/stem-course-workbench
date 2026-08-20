import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CourseModelPicker } from './CourseModelPicker'

describe('CourseModelPicker', () => {
  const options = [
    {
      adapter: 'open_notebook' as const,
      model: 'model:deepseek',
      reasoning_effort: null,
      optional: true,
      configured: true,
      selectable: true,
      name: 'DeepSeek V4 Pro',
      provider: 'deepseek',
    },
    {
      adapter: 'open_notebook' as const,
      model: null,
      reasoning_effort: null,
      optional: true,
      configured: false,
      selectable: false,
      display_name: 'deepseek-v4-pro',
    },
  ]

  it('submits the real model record ID and disables unconfigured options', () => {
    const onChange = vi.fn()
    render(<CourseModelPicker options={options} value={null} onChange={onChange} />)

    const unavailable = screen.getByRole('option', { name: /deepseek-v4-pro/ })
    expect(unavailable).toBeDisabled()

    fireEvent.change(screen.getByLabelText('course.modelLabel'), {
      target: { value: 'open_notebook|model:deepseek' },
    })

    expect(onChange).toHaveBeenCalledWith({
      adapter: 'open_notebook',
      model: 'model:deepseek',
      reasoning_effort: null,
    })
  })

  it('localizes every visible reasoning-effort option', () => {
    render(
      <CourseModelPicker
        options={[{
          adapter: 'codex_cli',
          model: 'gpt-5.6-sol',
          reasoning_effort: 'max',
          optional: false,
          configured: true,
          selectable: true,
        }]}
        value={{ adapter: 'codex_cli', model: 'gpt-5.6-sol', reasoning_effort: 'max' }}
        onChange={vi.fn()}
      />
    )

    for (const key of ['course.effortLow', 'course.effortMedium', 'course.effortHigh', 'course.effortXhigh', 'course.effortMax']) {
      expect(screen.getByRole('option', { name: key })).toBeVisible()
    }
  })

  it('shows an actionable configuration message when no model option exists', () => {
    render(<CourseModelPicker options={[]} value={null} onChange={vi.fn()} />)

    expect(screen.getByText('course.noSelectableModels')).toBeVisible()
  })

  it('uses independent ids for multiple staged model pickers', () => {
    const { container } = render(
      <>
        <CourseModelPicker idPrefix="review" accessibleLabel="Independent review" options={options} value={null} onChange={vi.fn()} />
        <CourseModelPicker idPrefix="escalation" accessibleLabel="High-risk escalation" options={options} value={null} onChange={vi.fn()} />
      </>
    )

    expect(container.querySelector('#review-model')).toBeVisible()
    expect(container.querySelector('#escalation-model')).toBeVisible()
    expect(container.querySelectorAll('#course-model')).toHaveLength(0)
    expect(screen.getByLabelText('Independent review — course.modelLabel')).toBeVisible()
    expect(screen.getByLabelText('High-risk escalation — course.modelLabel')).toBeVisible()
  })
})
