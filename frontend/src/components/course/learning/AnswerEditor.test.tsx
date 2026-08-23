import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import {
  AnswerEditor,
  emptyLearnerAnswer,
  isLearnerAnswerComplete,
} from './AnswerEditor'
import type { CourseAnswerFormat } from '@/lib/types/course'

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { index?: number }) => (
      values?.index ? `${key}-${values.index}` : key
    ),
  }),
}))

function Harness({ format }: { format: CourseAnswerFormat }) {
  const [value, setValue] = useState(() => emptyLearnerAnswer(format))
  return (
    <>
      <AnswerEditor format={format} value={value} onChange={setValue} />
      <output data-testid="answer-value">{JSON.stringify(value)}</output>
      <output data-testid="answer-complete">{String(isLearnerAnswerComplete(format, value))}</output>
    </>
  )
}

const scalarFormat = (kind: CourseAnswerFormat['kind']): CourseAnswerFormat => ({
  kind, component_count: null, unit_required: false, parts: [],
})

describe('AnswerEditor', () => {
  it.each([
    ['numeric', '4'],
    ['symbolic', 'x^2'],
    ['proof', 'Apply the definition.'],
    ['explanation', 'The graph approaches four.'],
  ] as const)('serializes a %s answer as bounded text', (kind, answer) => {
    render(<Harness format={scalarFormat(kind)} />)
    fireEvent.change(screen.getByRole('textbox', { name: 'course.exerciseAnswer' }), {
      target: { value: answer },
    })

    expect(screen.getByTestId('answer-value')).toHaveTextContent(JSON.stringify(answer))
    expect(screen.getByTestId('answer-complete')).toHaveTextContent('true')
  })

  it('serializes a unit-bearing answer as value plus unit', () => {
    render(<Harness format={{
      kind: 'unit', component_count: null, unit_required: true, parts: [],
    }} />)
    fireEvent.change(screen.getByRole('textbox', { name: 'course.answerValue' }), {
      target: { value: '9.8' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: 'course.answerUnit' }), {
      target: { value: 'm/s^2' },
    })

    expect(screen.getByTestId('answer-value')).toHaveTextContent(
      JSON.stringify({ value: '9.8', unit: 'm/s^2' }),
    )
    expect(screen.getByTestId('answer-complete')).toHaveTextContent('true')
  })

  it('serializes every vector component and its required unit', () => {
    render(<Harness format={{
      kind: 'vector', component_count: 2, unit_required: true, parts: [],
    }} />)
    const components = screen.getAllByRole('textbox', { name: /course.vectorComponent/ })
    fireEvent.change(components[0], { target: { value: '3' } })
    fireEvent.change(components[1], { target: { value: '4' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'course.answerUnit' }), {
      target: { value: 'm/s' },
    })

    expect(screen.getByTestId('answer-value')).toHaveTextContent(
      JSON.stringify({ components: ['3', '4'], unit: 'm/s' }),
    )
    expect(screen.getByTestId('answer-complete')).toHaveTextContent('true')
  })

  it('serializes set and multipart answers using the grader contract', () => {
    const multipart: CourseAnswerFormat = {
      kind: 'multipart', component_count: null, unit_required: false,
      parts: [
        scalarFormat('numeric'),
        { kind: 'set', component_count: null, unit_required: false, parts: [] },
      ],
    }
    render(<Harness format={multipart} />)
    fireEvent.change(screen.getByRole('textbox', { name: 'course.exerciseAnswer' }), {
      target: { value: '4' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: 'course.setItems' }), {
      target: { value: '1, 2\n3' },
    })

    expect(screen.getByTestId('answer-value')).toHaveTextContent(
      JSON.stringify({ parts: ['4', { items: ['1', '2', '3'] }] }),
    )
    expect(screen.getByTestId('answer-complete')).toHaveTextContent('true')
  })
})
