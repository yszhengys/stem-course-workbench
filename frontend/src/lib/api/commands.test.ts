import { describe, expect, it } from 'vitest'

import { commandJobStatusSchema } from './commands'

const response = (status: string) => ({
  job_id: 'command:one',
  status,
})

describe('commandJobStatusSchema', () => {
  it.each([
    'new',
    'running',
    'completed',
    'failed',
    'canceled',
    'queued',
    'succeeded',
    'cancelled',
  ])('accepts supported framework or Course status %s', (status) => {
    expect(commandJobStatusSchema.parse(response(status)).status).toBe(status)
  })

  it.each(['unknown', 'pending', 'submitted', 'COMPLETED', '']) (
    'rejects unsupported status %s',
    (status) => {
      expect(() => commandJobStatusSchema.parse(response(status))).toThrow()
    }
  )
})
