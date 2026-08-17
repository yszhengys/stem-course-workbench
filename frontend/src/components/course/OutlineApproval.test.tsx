import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { OutlineApproval } from './OutlineApproval'

describe('OutlineApproval', () => {
  it('only enables approval for the exact confirmation phrase', () => {
    const onApprove = vi.fn()
    render(<OutlineApproval disabled={false} onApprove={onApprove} />)
    const input = screen.getByLabelText('course.approvalLabel')
    const button = screen.getByRole('button', { name: 'course.approveOutline' })

    fireEvent.change(input, { target: { value: ' 确认大纲' } })
    expect(button).toBeDisabled()
    fireEvent.change(input, { target: { value: '确认大纲' } })
    expect(button).toBeEnabled()
    fireEvent.click(button)
    expect(onApprove).toHaveBeenCalledWith('确认大纲')
  })
})
