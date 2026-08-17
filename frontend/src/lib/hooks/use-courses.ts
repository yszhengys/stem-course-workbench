import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { courseApi } from '@/lib/api/course'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'
import type {
  BuildEvidenceRequest,
  CreateCourseRequest,
  GenerateChapterRequest,
  GenerateOutlineRequest,
} from '@/lib/types/course'

function useMutationFeedback() {
  const { toast } = useToast()
  const { t } = useTranslation()
  return {
    success: () => toast({
      title: t('common.success'),
      description: t('course.operationSuccess'),
    }),
    error: (error: unknown) => toast({
      title: t('common.error'),
      description: error instanceof Error ? error.message : t('course.operationFailed'),
      variant: 'destructive',
    }),
  }
}

export function useCourses() {
  return useQuery({ queryKey: QUERY_KEYS.courses, queryFn: courseApi.list, retry: false })
}

export function useCourse(courseId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.course(courseId),
    queryFn: () => courseApi.get(courseId),
    enabled: Boolean(courseId),
    retry: false,
  })
}

export function useEligibleCourseSources(courseId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.courseSources(courseId),
    queryFn: () => courseApi.listEligibleSources(courseId),
    enabled: Boolean(courseId),
    retry: false,
  })
}

export function useCourseAnchors(courseId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.courseAnchors(courseId),
    queryFn: () => courseApi.listAnchors(courseId),
    enabled: Boolean(courseId),
    retry: false,
  })
}

export function useCourseModelOptions() {
  return useQuery({
    queryKey: QUERY_KEYS.courseModels,
    queryFn: courseApi.getModelOptions,
    retry: false,
  })
}

export function useCurrentCourseOutline(courseId: string, enabled = true) {
  return useQuery({
    queryKey: QUERY_KEYS.courseOutline(courseId),
    queryFn: () => courseApi.getCurrentOutline(courseId),
    enabled: Boolean(courseId) && enabled,
    retry: false,
  })
}

export function useCurrentCourseChapter(courseId: string, chapterKey: string, enabled = true) {
  return useQuery({
    queryKey: QUERY_KEYS.courseChapter(courseId, chapterKey),
    queryFn: () => courseApi.getCurrentChapter(courseId, chapterKey),
    enabled: Boolean(courseId && chapterKey) && enabled,
    retry: false,
  })
}

export function useCourseFindings(courseId: string, chapterKey?: string) {
  return useQuery({
    queryKey: QUERY_KEYS.courseFindings(courseId, chapterKey),
    queryFn: () => courseApi.listFindings(courseId, chapterKey),
    enabled: Boolean(courseId),
    retry: false,
  })
}

export function useCourseProgress(courseId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.courseProgress(courseId),
    queryFn: () => courseApi.listProgress(courseId),
    enabled: Boolean(courseId),
    retry: false,
  })
}

export function useCourseNotes(courseId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.courseNotes(courseId),
    queryFn: () => courseApi.listNotes(courseId),
    enabled: Boolean(courseId),
    retry: false,
  })
}

export function useCreateCourse() {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: CreateCourseRequest) => courseApi.create(request),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: QUERY_KEYS.courses })
      feedback.success()
    },
    onError: feedback.error,
  })
}

export function useAssociateCourseSource(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: { source_id: string; role: 'PRIMARY' | 'SUPPLEMENT' }) =>
      courseApi.associateSource(courseId, request),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUERY_KEYS.course(courseId) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseSources(courseId) }),
      ])
      feedback.success()
    },
    onError: feedback.error,
  })
}

export function useBuildCourseEvidence(courseId: string) {
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: BuildEvidenceRequest) => courseApi.buildEvidence(courseId, request),
    onSuccess: feedback.success,
    onError: feedback.error,
  })
}

export function useGenerateCourseOutline(courseId: string) {
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: GenerateOutlineRequest) => courseApi.generateOutline(courseId, request),
    onSuccess: feedback.success,
    onError: feedback.error,
  })
}

export function useApproveCourseOutline(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: { version_id: string; confirmation: string }) =>
      courseApi.approveOutline(courseId, request),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUERY_KEYS.course(courseId) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseOutline(courseId) }),
      ])
      feedback.success()
    },
    onError: feedback.error,
  })
}

export function useGenerateCourseChapter(courseId: string, chapterKey: string) {
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: GenerateChapterRequest) =>
      courseApi.generateChapter(courseId, chapterKey, request),
    onSuccess: feedback.success,
    onError: feedback.error,
  })
}

export function useReviewCourseChapter(courseId: string, chapterKey: string) {
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: GenerateChapterRequest) =>
      courseApi.reviewChapter(courseId, chapterKey, request),
    onSuccess: feedback.success,
    onError: feedback.error,
  })
}

export function useUpdateCourseFinding(courseId: string, chapterKey?: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: ({
      findingId,
      ...request
    }: {
      findingId: string
      status: 'resolved' | 'acknowledged'
      resolution_reason: string
    }) => courseApi.updateFinding(courseId, findingId, request),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseFindings(courseId, chapterKey) }),
        chapterKey
          ? client.invalidateQueries({ queryKey: QUERY_KEYS.courseChapter(courseId, chapterKey) })
          : Promise.resolve(),
      ])
      feedback.success()
    },
    onError: feedback.error,
  })
}

export function useUpdateCourseProgress(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: Parameters<typeof courseApi.updateProgress>[1]) =>
      courseApi.updateProgress(courseId, request),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: QUERY_KEYS.courseProgress(courseId) })
      feedback.success()
    },
    onError: feedback.error,
  })
}

export function useCreateCourseNote(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: Parameters<typeof courseApi.createNote>[1]) =>
      courseApi.createNote(courseId, request),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: QUERY_KEYS.courseNotes(courseId) })
      feedback.success()
    },
    onError: feedback.error,
  })
}

export function useDeleteCourseNote(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (noteId: string) => courseApi.deleteNote(courseId, noteId),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: QUERY_KEYS.courseNotes(courseId) })
      feedback.success()
    },
    onError: feedback.error,
  })
}
