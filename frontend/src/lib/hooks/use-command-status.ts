import { QueryKey, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { commandsApi } from '@/lib/api/commands'

export const COMMAND_POLL_INTERVAL_MS = 2000
export const COMMAND_POLL_TIMEOUT_MS = 30 * 60 * 1000

const SUCCESS_STATES = new Set(['completed', 'succeeded'])
const FAILURE_STATES = new Set(['failed', 'cancelled', 'canceled'])

export function useCommandStatus(
  commandId: string | undefined,
  invalidateKeys: readonly QueryKey[],
) {
  const queryClient = useQueryClient()
  const [isTimedOut, setIsTimedOut] = useState(false)
  const invalidatedCommandRef = useRef<string | null>(null)

  useEffect(() => {
    setIsTimedOut(false)
    invalidatedCommandRef.current = null
  }, [commandId])

  const query = useQuery({
    queryKey: ['commands', 'jobs', commandId],
    queryFn: () => commandsApi.getStatus(commandId as string),
    enabled: Boolean(commandId) && !isTimedOut,
    retry: false,
    refetchInterval: (currentQuery) => {
      if (currentQuery.state.error) return false
      const status = currentQuery.state.data?.status?.toLowerCase()
      if (isTimedOut || (status && (SUCCESS_STATES.has(status) || FAILURE_STATES.has(status)))) {
        return false
      }
      return COMMAND_POLL_INTERVAL_MS
    },
    refetchIntervalInBackground: true,
  })

  const status = query.isError ? 'failed' : query.data?.status.toLowerCase()
  const isSuccess = Boolean(status && SUCCESS_STATES.has(status))
  const isFailure = query.isError || Boolean(status && FAILURE_STATES.has(status))

  useEffect(() => {
    if (!commandId || isSuccess || isFailure) return
    const timer = window.setTimeout(() => setIsTimedOut(true), COMMAND_POLL_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }, [commandId, isFailure, isSuccess])

  useEffect(() => {
    if (!commandId || !isSuccess || invalidatedCommandRef.current === commandId) return
    invalidatedCommandRef.current = commandId
    for (const queryKey of invalidateKeys) {
      void queryClient.invalidateQueries({ queryKey })
    }
  }, [commandId, invalidateKeys, isSuccess, queryClient])

  return {
    ...query,
    status: query.isError ? 'failed' : (query.data?.status ?? (commandId ? 'new' : undefined)),
    isSuccess,
    isFailure,
    isTimedOut,
    errorMessage: query.isError
      ? (query.error instanceof Error ? query.error.message : 'Unable to read task status')
      : (query.data?.error_message ?? null),
  }
}
