import { describe, expect, it, vi } from 'vitest'

import { submitEvidenceSource } from './evidence-source'
import type { EligibleCourseSource } from '@/lib/types/course'

const sources: EligibleCourseSource[] = [{
  source_id: 'source:listed',
  title: 'Slides',
  filename: 'slides.pptx',
  kind: 'pptx',
  role: 'SUPPLEMENT',
  associated: true,
}]

describe('submitEvidenceSource', () => {
  it('associates and builds a manually entered Source ID without sending a path', async () => {
    const associate = vi.fn().mockResolvedValue(undefined)
    const build = vi.fn().mockResolvedValue({ command_id: 'command:one' })

    await submitEvidenceSource({
      sourceId: '  source:manual  ',
      role: 'PRIMARY',
      sources,
      associate,
      build,
    })

    expect(associate).toHaveBeenCalledWith({ source_id: 'source:manual', role: 'PRIMARY' })
    expect(build).toHaveBeenCalledWith({ source_id: 'source:manual', role: 'PRIMARY', force: false })
    expect(JSON.stringify(build.mock.calls)).not.toContain('path')
  })

  it('surfaces an invalid manual Source error and never queues evidence', async () => {
    const error = new Error('Source does not belong to this course Notebook')
    const associate = vi.fn().mockRejectedValue(error)
    const build = vi.fn()

    await expect(submitEvidenceSource({
      sourceId: 'source:invalid',
      role: 'PRIMARY',
      sources,
      associate,
      build,
    })).rejects.toBe(error)

    expect(build).not.toHaveBeenCalled()
  })
})
