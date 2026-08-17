import { useQuery } from '@tanstack/react-query'
import { insightsApi } from '@/lib/api/insights'

export function useInsight(id: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ['insights', id],
    queryFn: () => insightsApi.get(id),
    enabled: options?.enabled !== false && !!id,
    staleTime: 30 * 1000, // 30 seconds
  })
}

/**
 * Insights belonging to one source. Preserves the previous manual fetch
 * semantics: fresh on mount, single attempt (no retry), refreshed by
 * invalidating ['insights', 'source', sourceId] after create/delete.
 */
export function useSourceInsights(sourceId: string) {
  return useQuery({
    queryKey: ['insights', 'source', sourceId],
    queryFn: () => insightsApi.listForSource(sourceId),
    enabled: !!sourceId,
    staleTime: 0,
    retry: false,
  })
}
