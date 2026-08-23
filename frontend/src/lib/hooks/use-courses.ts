import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { courseApi } from '@/lib/api/course'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'
import type {
  BuildEvidenceRequest,
  CourseExerciseGradeRequest,
  CourseExerciseHintRequest,
  CourseExerciseRevealRequest,
  CourseLearningEventRequest,
  CourseTransferGradeRequest,
  CourseTutorMessageRequest,
  CourseTutorSessionCreateRequest,
  CreateCourseRequest,
  CreateCourseAttemptRequest,
  GenerateChapterRequest,
  GenerateOutlineRequest,
  ReviewChapterRequest,
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

export function useCourseLabs(courseId: string, chapterKey: string, enabled = true) {
  return useQuery({
    queryKey: QUERY_KEYS.courseLabs(courseId, chapterKey),
    queryFn: () => courseApi.listChapterLabs(courseId, chapterKey),
    enabled: Boolean(courseId && chapterKey) && enabled,
    retry: false,
  })
}

export function useCourseAttempts(courseId: string, chapterKey: string, enabled = true) {
  return useQuery({
    queryKey: QUERY_KEYS.courseAttempts(courseId, chapterKey),
    queryFn: () => courseApi.listChapterAttempts(courseId, chapterKey),
    enabled: Boolean(courseId && chapterKey) && enabled,
    retry: false,
  })
}

export function useCourseLearningOverview(courseId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.courseLearningOverview(courseId),
    queryFn: () => courseApi.getLearningOverview(courseId),
    enabled: Boolean(courseId),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: true,
  })
}

export function useCourseLearningChapter(
  courseId: string,
  chapterKey: string,
  enabled = true,
) {
  return useQuery({
    queryKey: QUERY_KEYS.courseLearningChapter(courseId, chapterKey),
    queryFn: () => courseApi.getLearningChapter(courseId, chapterKey),
    enabled: Boolean(courseId && chapterKey) && enabled,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: true,
  })
}

export function useCourseLearningSources(
  courseId: string,
  chapterKey: string,
  enabled = true,
) {
  return useQuery({
    queryKey: QUERY_KEYS.courseLearningSources(courseId, chapterKey),
    queryFn: () => courseApi.getLearningSources(courseId, chapterKey),
    enabled: Boolean(courseId && chapterKey) && enabled,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: true,
  })
}

export function useCourseLearningNotes(
  courseId: string,
  chapterKey: string,
  enabled = true,
) {
  return useQuery({
    queryKey: QUERY_KEYS.courseLearningNotes(courseId, chapterKey),
    queryFn: () => courseApi.getLearningNotes(courseId, chapterKey),
    enabled: Boolean(courseId && chapterKey) && enabled,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: true,
  })
}

export function useCourseTutorSessions(courseId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.courseTutorSessions(courseId),
    queryFn: () => courseApi.listTutorSessions(courseId),
    enabled: Boolean(courseId),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: true,
  })
}

export function useCourseReviewQueue(courseId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.courseReviewQueue(courseId),
    queryFn: () => courseApi.getReviewQueue(courseId),
    enabled: Boolean(courseId),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: true,
  })
}

export function useCourseExercises(
  courseId: string,
  chapterKey?: string,
  enabled = true,
) {
  return useQuery({
    queryKey: QUERY_KEYS.courseExercises(courseId, chapterKey),
    queryFn: () => courseApi.listLearningExercises(courseId, chapterKey),
    enabled: Boolean(courseId) && enabled,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: true,
  })
}

export function useCourseChapterDraft(
  courseId: string,
  chapterKey: string,
  enabled = true,
) {
  return useQuery({
    queryKey: QUERY_KEYS.courseChapterDraft(courseId, chapterKey),
    queryFn: () => courseApi.getChapterDraft(courseId, chapterKey),
    enabled: Boolean(courseId && chapterKey) && enabled,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: true,
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
    mutationFn: (request: ReviewChapterRequest) =>
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

export function useReattachCourseNote(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: ({ noteId, ...request }: {
      noteId: string
      chapter_key: string
      block_key: string
    }) => courseApi.reattachNote(courseId, noteId, request),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: QUERY_KEYS.courseNotes(courseId) })
      feedback.success()
    },
    onError: feedback.error,
  })
}

export function useCreateCourseAttempt(courseId: string, chapterKey: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: ({ labKey, request }: { labKey: string; request: CreateCourseAttemptRequest }) =>
      courseApi.createChapterAttempt(courseId, chapterKey, labKey, request),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: QUERY_KEYS.courseAttempts(courseId, chapterKey) })
      feedback.success()
    },
    onError: feedback.error,
  })
}

export function usePublishCourseChapter(courseId: string, chapterKey: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: () => courseApi.publishChapter(courseId, chapterKey),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUERY_KEYS.course(courseId) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseChapter(courseId, chapterKey) }),
      ])
      feedback.success()
    },
    onError: feedback.error,
  })
}

export function useApplyCourseChapterDraftOperation(
  courseId: string,
  chapterKey: string,
) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: Parameters<typeof courseApi.applyChapterDraftOperation>[2]) =>
      courseApi.applyChapterDraftOperation(courseId, chapterKey, request),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseChapterDraft(courseId, chapterKey) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseChapter(courseId, chapterKey) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseFindings(courseId, chapterKey) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseLabs(courseId, chapterKey) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseExercises(courseId, chapterKey) }),
      ])
      feedback.success()
    },
    onError: feedback.error,
    retry: false,
  })
}

export function useValidateCourseChapterDraft(
  courseId: string,
  chapterKey: string,
) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: Parameters<typeof courseApi.validateChapterDraft>[2]) =>
      courseApi.validateChapterDraft(courseId, chapterKey, request),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseChapterDraft(courseId, chapterKey) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseChapter(courseId, chapterKey) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseFindings(courseId, chapterKey) }),
      ])
      feedback.success()
    },
    onError: feedback.error,
    retry: false,
  })
}

export function useAppendCourseLearningEvent(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: CourseLearningEventRequest) =>
      courseApi.appendLearningEvent(courseId, request),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseLearningOverview(courseId) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseReviewQueue(courseId) }),
      ])
    },
    onError: feedback.error,
    retry: false,
  })
}

export function useGradeCourseExercise(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: ({
      exerciseKey,
      request,
    }: {
      exerciseKey: string
      request: CourseExerciseGradeRequest
    }) => courseApi.gradeLearningExercise(courseId, exerciseKey, request),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseLearningOverview(courseId) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseReviewQueue(courseId) }),
      ])
    },
    onError: feedback.error,
    retry: false,
  })
}

export function useNextCourseExerciseHint(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: ({
      exerciseKey,
      request,
    }: {
      exerciseKey: string
      request: CourseExerciseHintRequest
    }) => courseApi.requestNextHint(courseId, exerciseKey, request),
    onSuccess: async () => {
      await client.invalidateQueries({
        queryKey: QUERY_KEYS.courseLearningOverview(courseId),
      })
    },
    onError: feedback.error,
    retry: false,
  })
}

export function useRevealCourseExerciseAnswer(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: ({
      exerciseKey,
      request,
    }: {
      exerciseKey: string
      request: CourseExerciseRevealRequest
    }) => courseApi.revealExerciseAnswer(courseId, exerciseKey, request),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseLearningOverview(courseId) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseReviewQueue(courseId) }),
      ])
    },
    onError: feedback.error,
    retry: false,
  })
}

export function useGradeCourseTransfer(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: ({
      exerciseKey,
      request,
    }: {
      exerciseKey: string
      request: CourseTransferGradeRequest
    }) => courseApi.gradeTransfer(courseId, exerciseKey, request),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseLearningOverview(courseId) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseReviewQueue(courseId) }),
      ])
    },
    onError: feedback.error,
    retry: false,
  })
}

export function useCreateCourseLearningNote(courseId: string, chapterKey: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: Parameters<typeof courseApi.createLearningNote>[2]) =>
      courseApi.createLearningNote(courseId, chapterKey, request),
    onSuccess: async () => {
      await client.invalidateQueries({
        queryKey: QUERY_KEYS.courseLearningNotes(courseId, chapterKey),
      })
      feedback.success()
    },
    onError: feedback.error,
    retry: false,
  })
}

export function useCreateCourseTutorSession(courseId: string) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: CourseTutorSessionCreateRequest) =>
      courseApi.createTutorSession(courseId, request),
    onSuccess: async () => {
      await client.invalidateQueries({
        queryKey: QUERY_KEYS.courseTutorSessions(courseId),
      })
      feedback.success()
    },
    onError: feedback.error,
    retry: false,
  })
}

export function useSendCourseTutorMessage(
  courseId: string,
  sessionId: string | undefined,
) {
  const client = useQueryClient()
  const feedback = useMutationFeedback()
  return useMutation({
    mutationFn: (request: CourseTutorMessageRequest) => {
      if (!sessionId) throw new Error('Tutor session is required')
      return courseApi.sendTutorMessage(courseId, sessionId, request)
    },
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseTutorSessions(courseId) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseLearningOverview(courseId) }),
        client.invalidateQueries({ queryKey: QUERY_KEYS.courseReviewQueue(courseId) }),
      ])
    },
    onError: feedback.error,
    retry: false,
  })
}
