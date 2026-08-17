import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PropsWithChildren } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { commandsApi } from '@/lib/api/commands'
import {
  COMMAND_POLL_INTERVAL_MS,
  COMMAND_POLL_TIMEOUT_MS,
  useCommandStatus,
} from './use-command-status'

vi.mock('@/lib/api/commands', () => ({
  commandsApi: { getStatus: vi.fn() },
}))

describe('useCommandStatus', () => {
  let queryClient: QueryClient
  let invalidate: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    })
    invalidate = vi.spyOn(queryClient, 'invalidateQueries')
  })

  afterEach(() => {
    queryClient.clear()
    vi.useRealTimers()
  })

  const flush = async () => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
      await Promise.resolve()
      await Promise.resolve()
    })
  }

  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )

  it.each(['completed', 'succeeded'])('stops on %s and invalidates once', async (terminal) => {
    vi.mocked(commandsApi.getStatus)
      .mockResolvedValueOnce({ job_id: 'command:one', status: 'running' })
      .mockResolvedValueOnce({ job_id: 'command:one', status: terminal })

    const { result } = renderHook(
      () => useCommandStatus('command:one', [['courses'], ['course', 'one']]),
      { wrapper }
    )

    await flush()
    expect(result.current.status).toBe('running')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(COMMAND_POLL_INTERVAL_MS + 1)
    })
    await flush()
    expect(result.current.isSuccess).toBe(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(COMMAND_POLL_INTERVAL_MS * 2)
    })

    expect(commandsApi.getStatus).toHaveBeenCalledTimes(2)
    expect(invalidate).toHaveBeenCalledTimes(2)
  })

  it.each(['failed', 'cancelled', 'canceled'])('stops and exposes backend failure for %s', async (terminal) => {
    vi.mocked(commandsApi.getStatus).mockResolvedValue({
      job_id: 'command:one',
      status: terminal,
      error_message: 'worker failed',
    })

    const { result } = renderHook(
      () => useCommandStatus('command:one', [['courses']]),
      { wrapper }
    )

    await flush()
    expect(result.current.isFailure).toBe(true)
    expect(result.current.errorMessage).toBe('worker failed')
    await vi.advanceTimersByTimeAsync(COMMAND_POLL_INTERVAL_MS * 2)
    expect(commandsApi.getStatus).toHaveBeenCalledTimes(1)
  })

  it('times out after thirty minutes without retrying or invalidating', async () => {
    vi.mocked(commandsApi.getStatus).mockResolvedValue({
      job_id: 'command:one',
      status: 'running',
    })

    const { result } = renderHook(
      () => useCommandStatus('command:one', [['courses']]),
      { wrapper }
    )

    await act(async () => {
      await vi.advanceTimersByTimeAsync(COMMAND_POLL_TIMEOUT_MS)
    })

    expect(result.current.isTimedOut).toBe(true)
    expect(invalidate).not.toHaveBeenCalled()
  })
})
