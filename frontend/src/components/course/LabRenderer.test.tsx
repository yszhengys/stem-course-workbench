import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { LabRenderer } from './LabRenderer'

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) => values
      ? `${key}:${Object.entries(values).map(([name, value]) => `${name}=${value}`).join(',')}`
      : key,
  }),
}))

const base = {
  key: 'lab', title: 'Lab', anchor_ids: [], provenance: 'pedagogical', controls: [], objects: [],
}

const pedagogy = {
  learning_objectives: ['Relate a parameter to the graph.'],
  prerequisite_concepts: ['Cartesian coordinates'],
  variables: [{ key: 'a', label: 'Slope', unit: null, range: [-2, 2] as [number, number] }],
  prediction_prompt: 'Predict how the graph changes.',
  steps: ['Record a prediction.', 'Move the slider.'],
  expected_observations: ['The slope increases with a.'],
  student_submission: 'Submit a prediction and an observation.',
  rubric: ['States the direction of change.', 'Uses graph evidence.'],
  error_boundaries: ['Only claim behavior inside the displayed domain.'],
  accessible_alternative: 'Use the data table to compare values.',
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
    expect(screen.getByRole('table', { name: 'course.labDataAlternative' })).toBeVisible()
    expect(screen.getByText('course.labTextAlternative')).toBeVisible()
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

  it('gives repeated lab instances unique control and alternative identifiers', () => {
    const controlled = {
      ...base,
      kind: 'function_plot', expressions: ['a*x'], domain: { x: [-2, 2] },
      controls: [{ key: 'a', label: 'Slope', min: -2, max: 2, value: 1 }],
    }
    render(<><LabRenderer spec={controlled} /><LabRenderer spec={controlled} /></>)

    const sliders = screen.getAllByRole('slider', { name: 'Slope' })
    expect(sliders).toHaveLength(2)
    expect(sliders[0].id).not.toBe(sliders[1].id)
    const canvases = screen.getAllByTestId('safe-lab-canvas')
    expect(canvases[0].getAttribute('aria-describedby')).not.toBe(
      canvases[1].getAttribute('aria-describedby'),
    )
  })

  it('preserves series identity and reports displayed versus total samples', () => {
    render(<LabRenderer spec={{
      ...base, kind: 'function_plot', expressions: ['x', 'x^2'], domain: { x: [-2, 2] },
    }} />)

    const table = screen.getByRole('table', { name: 'course.labDataAlternative' })
    expect(within(table).getByRole('columnheader', { name: 'course.labSeries' })).toBeVisible()
    expect(within(table).getAllByTestId(/^lab-series-1-/).length).toBeGreaterThan(0)
    expect(within(table).getAllByTestId(/^lab-series-2-/).length).toBeGreaterThan(0)
    expect(screen.getByText(
      'course.labSampleSummary:paths=2,displayed=20,total=500',
    )).toBeVisible()
  })

  it('provides x, y, u and v columns for the vector-field alternative', () => {
    render(<LabRenderer spec={{
      ...base, kind: 'vector_field', expressions: ['-y', 'x'],
      domain: { x: [-2, 2], y: [-2, 2] },
    }} />)

    const table = screen.getByRole('table', { name: 'course.labDataAlternative' })
    expect(within(table).getByRole('columnheader', { name: 'course.labVectorIndex' })).toBeVisible()
    expect(within(table).getByRole('columnheader', { name: 'course.labVectorU' })).toBeVisible()
    expect(within(table).getByRole('columnheader', { name: 'course.labVectorV' })).toBeVisible()
    expect(within(table).getAllByRole('row')).toHaveLength(21)
  })

  it('makes the scrollable data alternative keyboard focusable', () => {
    render(<LabRenderer spec={{
      ...base, kind: 'parametric_curve', expressions: ['cos(t)', 'sin(t)'],
      domain: { t: [0, 6.28] },
    }} />)

    expect(screen.getByRole('region', { name: 'course.labScrollableData' })).toHaveAttribute(
      'tabindex', '0',
    )
  })

  it('renders the prediction, procedure, rubric and accessible pedagogy as text', () => {
    render(<LabRenderer spec={{
      ...base, kind: 'function_plot', expressions: ['a*x'], domain: { x: [-2, 2] },
      controls: [{ key: 'a', label: 'Slope', min: -2, max: 2, value: 1 }],
      pedagogy,
    }} />)

    expect(screen.getByText('Predict how the graph changes.')).toBeVisible()
    expect(screen.getByText('Record a prediction.')).toBeVisible()
    expect(screen.getByText('The slope increases with a.')).toBeVisible()
    expect(screen.getByText('Submit a prediction and an observation.')).toBeVisible()
    expect(screen.getByText('States the direction of change.')).toBeVisible()
    expect(screen.getByText('Use the data table to compare values.')).toBeVisible()
    expect(screen.getByText('course.labPrediction')).toBeVisible()
  })

  it('fails closed instead of rendering executable pedagogy markup', () => {
    render(<LabRenderer spec={{
      ...base, kind: 'function_plot', expressions: ['x'], domain: { x: [-2, 2] },
      pedagogy: { ...pedagogy, accessible_alternative: '<script>alert(1)</script>' },
    }} />)

    expect(screen.getByText('course.invalidLab')).toBeVisible()
    expect(document.querySelector('script')).not.toBeInTheDocument()
  })
})
