import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/lib/api/client'
import { courseApi } from './course'

vi.mock('@/lib/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

describe('courseApi', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('submits Source identity and role without a server path', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        command_id: 'command:evidence',
        run_id: 'course_generation_run:evidence',
        status: 'queued',
      },
    })

    await courseApi.buildEvidence('course:one', {
      source_id: 'source:pdf',
      role: 'PRIMARY',
      force: false,
    })

    expect(apiClient.post).toHaveBeenCalledWith(
      '/courses/course%3Aone/evidence/build',
      { source_id: 'source:pdf', role: 'PRIMARY', force: false }
    )
  })

  it('submits the real Open Notebook model record ID', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: {
        command_id: 'command:outline',
        run_id: 'course_generation_run:outline',
        status: 'queued',
      },
    })

    await courseApi.generateOutline('course:one', {
      anchor_ids: ['anchor:one'],
      available_lab_keys: [],
      prompt_version: 'v1',
      force: false,
      model: {
        adapter: 'open_notebook',
        model: 'model:deepseek-v4-pro',
        reasoning_effort: null,
      },
    })

    expect(apiClient.post).toHaveBeenCalledWith(
      '/courses/course%3Aone/outline/generate',
      expect.objectContaining({
        model: expect.objectContaining({ model: 'model:deepseek-v4-pro' }),
      })
    )
  })
})
