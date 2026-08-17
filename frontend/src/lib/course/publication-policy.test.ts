import { describe, expect, it } from 'vitest'

import { isFindingBlockingPublication } from './publication-policy'

const finding = (severity: 'info' | 'warning' | 'high' | 'error', status: string) => ({
  severity,
  status,
})

describe('chapter publication policy mirror', () => {
  it('requires errors and manual checks to be resolved', () => {
    expect(isFindingBlockingPublication(finding('error', 'acknowledged'))).toBe(true)
    expect(isFindingBlockingPublication(finding('error', 'resolved'))).toBe(false)
    expect(isFindingBlockingPublication(finding('warning', 'manual_check'))).toBe(true)
  })

  it('allows high and warning findings only after resolution or acknowledgement', () => {
    expect(isFindingBlockingPublication(finding('high', 'open'))).toBe(true)
    expect(isFindingBlockingPublication(finding('high', 'acknowledged'))).toBe(false)
    expect(isFindingBlockingPublication(finding('warning', 'resolved'))).toBe(false)
    expect(isFindingBlockingPublication(finding('info', 'open'))).toBe(false)
  })
})
