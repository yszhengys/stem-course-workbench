import { QueryClient } from '@tanstack/react-query'
import { isNotFoundError } from '@/lib/utils/error-handler'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      gcTime: 10 * 60 * 1000, // 10 minutes
      // Retry transient failures, but never retry 404s: the item was
      // deleted (or never existed) and retrying cannot change that.
      retry: (failureCount, error) => !isNotFoundError(error) && failureCount < 2,
      refetchOnWindowFocus: false,
    },
    // Mutations must never auto-retry (project rule: no automatic request
    // retry) — a retried POST/DELETE duplicates side effects (sources,
    // podcast jobs). TanStack Query's default is 0; nothing to configure.
  },
})

export const QUERY_KEYS = {
  notebooks: ['notebooks'] as const,
  notebook: (id: string) => ['notebooks', id] as const,
  notes: (notebookId?: string) => ['notes', notebookId] as const,
  note: (id: string) => ['notes', id] as const,
  sources: (notebookId?: string) => ['sources', notebookId] as const,
  sourcesInfinite: (notebookId: string) => ['sources', 'infinite', notebookId] as const,
  source: (id: string) => ['sources', id] as const,
  settings: ['settings'] as const,
  sourceChatSessions: (sourceId: string) => ['source-chat', sourceId, 'sessions'] as const,
  sourceChatSession: (sourceId: string, sessionId: string) => ['source-chat', sourceId, 'sessions', sessionId] as const,
  notebookChatSessions: (notebookId: string) => ['notebook-chat', notebookId, 'sessions'] as const,
  notebookChatSession: (sessionId: string) => ['notebook-chat', 'sessions', sessionId] as const,
  podcastEpisodes: ['podcasts', 'episodes'] as const,
  podcastEpisode: (episodeId: string) => ['podcasts', 'episodes', episodeId] as const,
  episodeProfiles: ['podcasts', 'episode-profiles'] as const,
  speakerProfiles: ['podcasts', 'speaker-profiles'] as const,
  languages: ['languages'] as const,
  courses: ['courses'] as const,
  course: (id: string) => ['courses', id] as const,
  courseSources: (id: string) => ['courses', id, 'sources', 'eligible'] as const,
  courseAnchors: (id: string) => ['courses', id, 'evidence', 'anchors'] as const,
  courseModels: ['courses', 'model-options'] as const,
  courseOutline: (id: string) => ['courses', id, 'outline'] as const,
  courseChapter: (id: string, chapterKey: string) => ['courses', id, 'chapters', chapterKey] as const,
  courseFindings: (id: string, chapterKey?: string) => ['courses', id, 'findings', chapterKey] as const,
  courseProgress: (id: string) => ['courses', id, 'progress'] as const,
  courseNotes: (id: string) => ['courses', id, 'notes'] as const,
  courseLabs: (id: string, chapterKey: string) => ['courses', id, 'chapters', chapterKey, 'labs'] as const,
  courseAttempts: (id: string, chapterKey: string) => ['courses', id, 'chapters', chapterKey, 'attempts'] as const,
}
