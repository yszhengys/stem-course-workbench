import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { LabRenderer } from './LabRenderer'

const base = {
  key: 'lab', title: 'Lab', anchor_ids: [], controls: [], objects: [],
}

describe('LabRenderer', () => {
  it.each([
    { kind: 'function_plot', expressions: ['x^2'], domain: { x: [-2, 2] } },
    { kind: 'parametric_curve', expressions: ['cos(t)', 'sin(t)'], domain: { t: [0, 6.28] } },
    { kind: 'vector_field', expressions: ['-y', 'x'], domain: { x: [-2, 2], y: [-2, 2] } },
    { kind: 'geometry', expressions: [], domain: {}, objects: [{ type: 'point', x: 0, y: 0 }] },
    { kind: 'kinematics', expressions: ['t', 't^2'], domain: { t: [0, 2] } },
  ])('renders the $kind declarative variant without code execution', (variant) => {
    render(<LabRenderer spec={{ ...base, ...variant }} />)
    expect(screen.getByTestId('safe-lab-canvas')).toBeVisible()
    expect(screen.queryByText('course.invalidLab')).not.toBeInTheDocument()
  })

  it('renders a localized safe error for an invalid expression', () => {
    render(<LabRenderer spec={{
      ...base,
      kind: 'function_plot', expressions: ['window.alert(1)'], domain: { x: [-2, 2] },
    }} />)
    expect(screen.getByText('course.invalidLab')).toBeVisible()
    expect(screen.queryByTestId('safe-lab-canvas')).not.toBeInTheDocument()
  })
})
