import { describe, expect, it } from 'vitest'

import { isFindingBlockingPublication } from './publication-policy'

const finding = (
  severity: 'info' | 'warning' | 'high' | 'error',
  status: string,
  resolution_reason: string | null = null,
) => ({
  severity,
  status,
  resolution_reason,
})

describe('chapter publication policy mirror', () => {
  it('requires errors and manual checks to be resolved', () => {
    expect(isFindingBlockingPublication(finding('error', 'acknowledged'))).toBe(true)
    expect(isFindingBlockingPublication(finding('error', 'resolved', 'Corrected and rechecked.'))).toBe(false)
    expect(isFindingBlockingPublication(finding('warning', 'manual_check'))).toBe(true)
  })

  it('allows high and warning findings only after resolution or acknowledgement', () => {
    expect(isFindingBlockingPublication(finding('high', 'open'))).toBe(true)
    expect(isFindingBlockingPublication(finding('high', 'acknowledged', 'Accepted risk.'))).toBe(false)
    expect(isFindingBlockingPublication(finding('warning', 'resolved', 'Corrected.'))).toBe(false)
    expect(isFindingBlockingPublication(finding('info', 'open'))).toBe(false)
  })

  it.each(['resolved', 'acknowledged'])(
    'keeps a %s finding blocking until its resolution reason is nonblank',
    (status) => {
      expect(isFindingBlockingPublication(finding('high', status, null))).toBe(true)
      expect(isFindingBlockingPublication(finding('high', status, '   '))).toBe(true)
      expect(isFindingBlockingPublication(finding('high', status, 'Reviewed manually.'))).toBe(false)
    }
  )
})
