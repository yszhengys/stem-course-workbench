import { describe, expect, it } from 'vitest'

import { SAFE_LAB_PROPOSAL_KEYS } from './lab-proposals'

describe('SAFE_LAB_PROPOSAL_KEYS', () => {
  it('offers four unique declarative proposals for every supported Lab kind', () => {
    expect(SAFE_LAB_PROPOSAL_KEYS).toHaveLength(20)
    expect(new Set(SAFE_LAB_PROPOSAL_KEYS).size).toBe(20)

    for (const prefix of [
      'function-plot',
      'parametric-curve',
      'vector-field',
      'geometry',
      'kinematics',
    ]) {
      expect(SAFE_LAB_PROPOSAL_KEYS.filter((key) => key.startsWith(`${prefix}-`))).toHaveLength(4)
    }
  })
})
