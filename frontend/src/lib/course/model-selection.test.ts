import { describe, expect, it } from 'vitest'

import { selectableDefaultModel } from './model-selection'
import type { CourseModelOption, ModelSelection } from '@/lib/types/course'

const preferred: ModelSelection = {
  adapter: 'codex_cli',
  model: 'gpt-5.6-sol',
  reasoning_effort: 'max',
}

function option(overrides: Partial<CourseModelOption> = {}): CourseModelOption {
  return {
    adapter: 'codex_cli',
    model: 'gpt-5.6-sol',
    reasoning_effort: 'max',
    optional: false,
    configured: true,
    selectable: true,
    ...overrides,
  }
}

describe('selectableDefaultModel', () => {
  it('returns a default only when its exact adapter and model are selectable', () => {
    expect(selectableDefaultModel([option()], preferred)).toEqual(preferred)
    expect(selectableDefaultModel([option({ selectable: false })], preferred)).toBeNull()
    expect(selectableDefaultModel([option({ configured: false })], preferred)).toBeNull()
    expect(selectableDefaultModel([option({ model: 'gpt-5.6-luna' })], preferred)).toBeNull()
  })
})
