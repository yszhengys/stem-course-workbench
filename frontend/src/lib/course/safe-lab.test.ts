import { describe, expect, it } from 'vitest'

import { evaluateSafeExpression, sampleLab, validateLabSpec } from './safe-lab'

describe('safe Lab expression interpreter', () => {
  it('evaluates only declared variables, arithmetic and allowlisted functions', () => {
    expect(evaluateSafeExpression('sin(x)^2 + cos(x)^2', { x: 0.5 })).toBeCloseTo(1)
    expect(evaluateSafeExpression('sqrt(a^2 + b^2)', { a: 3, b: 4 })).toBe(5)
  })

  it.each([
    'x = 1',
    'window.alert(1)',
    'x; alert(1)',
    'constructor(1)',
    '[1, 2]',
    'x ? 1 : 0',
    '2x',
  ])('rejects executable or unsupported syntax: %s', (expression) => {
    expect(() => evaluateSafeExpression(expression, { x: 1 })).toThrow()
  })

  it('rejects unknown symbols and non-finite results', () => {
    expect(() => evaluateSafeExpression('secret + 1', { x: 1 })).toThrow(/Unknown symbol/)
    expect(() => evaluateSafeExpression('1 / 0', {})).toThrow(/finite/)
    expect(() => evaluateSafeExpression('2 ^ 9999', {})).toThrow(/finite|range/)
  })

  it('enforces expression, control and total sample limits', () => {
    const valid = validateLabSpec({
      kind: 'function_plot', key: 'plot', title: 'Plot', anchor_ids: [],
      provenance: 'pedagogical', expressions: ['x'], domain: { x: [-1, 1] }, controls: [], objects: [],
    })
    expect(sampleLab(valid).totalSamples).toBeLessThanOrEqual(1000)

    expect(() => validateLabSpec({
      kind: 'function_plot', key: 'plot', title: 'Plot', anchor_ids: [],
      provenance: 'pedagogical', expressions: Array.from({ length: 9 }, () => 'x'),
      domain: { x: [-1, 1] }, controls: [], objects: [],
    })).toThrow()

    expect(() => validateLabSpec({
      kind: 'parametric_curve', key: 'curve', title: 'Curve', anchor_ids: [],
      provenance: 'pedagogical', expressions: ['t'], domain: { t: [0, 1] }, controls: [], objects: [],
    })).toThrow(/two expressions/)

    expect(() => validateLabSpec({
      kind: 'geometry', key: 'geometry', title: 'Geometry', anchor_ids: [],
      provenance: 'pedagogical', expressions: [], domain: {}, controls: [],
      objects: [{ type: 'point', x: 1_000_001, y: 0 }],
    })).toThrow(/safe range/)
  })

  it('reports the number of samples that were actually rendered', () => {
    const singular = validateLabSpec({
      kind: 'function_plot', key: 'partial', title: 'Partial plot', anchor_ids: [],
      provenance: 'pedagogical', expressions: ['sqrt(x)'], domain: { x: [-1, 1] },
      controls: [], objects: [],
    })
    const sampled = sampleLab(singular)

    expect(sampled.totalSamples).toBe(sampled.paths.flat().length)
    expect(sampled.totalSamples).toBeLessThan(250)
  })
})
