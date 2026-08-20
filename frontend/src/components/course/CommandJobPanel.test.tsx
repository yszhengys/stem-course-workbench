import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { CommandJobPanel } from './CommandJobPanel'

describe('CommandJobPanel', () => {
  it('renders an unknown runtime status as a failure, never as a finished job', () => {
    render(<CommandJobPanel status={'mystery' as never} />)

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('course.operationFailed')
    expect(alert).not.toHaveTextContent('course.jobFinishedDescription')
  })
})
