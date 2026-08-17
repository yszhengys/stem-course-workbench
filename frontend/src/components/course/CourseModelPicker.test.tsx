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
})
