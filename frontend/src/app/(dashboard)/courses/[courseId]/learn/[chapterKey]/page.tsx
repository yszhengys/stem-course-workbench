'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft, Hammer } from 'lucide-react'

import { ChapterReader } from '@/components/course/learning/ChapterReader'
import {
  ChapterTutor,
  type TutorAttemptScope,
} from '@/components/course/learning/ChapterTutor'
import { ChapterNotes } from '@/components/course/learning/ChapterNotes'
import { ExerciseRunner } from '@/components/course/learning/ExerciseRunner'
import { LearnerSources } from '@/components/course/learning/LearnerSources'
import {
  CourseInlineError,
  CourseInlineLoading,
  CoursePageError,
  CoursePageLoading,
  CoursePageNotFound,
} from '@/components/course/CoursePageState'
import { AppShell } from '@/components/layout/AppShell'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  useAppendCourseLearningEvent,
  useCourse,
  useCourseExercises,
  useCourseLabs,
  useCourseLearningChapter,
  useCourseLearningNotes,
  useCourseLearningOverview,
  useCourseLearningSources,
  usePrepareCourseLearningUpgrade,
} from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'
import {
  conceptLabel,
  selectExerciseConcept,
} from '@/lib/course/mastery'
import { isNotFoundError } from '@/lib/utils/error-handler'

function sessionKey(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
    .slice(0, 100)
}

export default function CourseLearnChapterPage() {
  const { t } = useTranslation()
  const params = useParams()
  const courseId = params?.courseId ? decodeURIComponent(params.courseId as string) : ''
  const chapterKey = params?.chapterKey ? decodeURIComponent(params.chapterKey as string) : ''
  const course = useCourse(courseId)
  const overview = useCourseLearningOverview(courseId)
  const chapter = useCourseLearningChapter(courseId, chapterKey)
  const sources = useCourseLearningSources(courseId, chapterKey, Boolean(chapter.data))
  const notes = useCourseLearningNotes(courseId, chapterKey, Boolean(chapter.data))
  const exercises = useCourseExercises(courseId, chapterKey)
  const labs = useCourseLabs(courseId, chapterKey, Boolean(chapter.data))
  const appendEvent = useAppendCourseLearningEvent(courseId)
  const prepareUpgrade = usePrepareCourseLearningUpgrade(courseId)
  const openedKey = useRef(sessionKey('chapter-open'))
  const upgradeKey = useRef(sessionKey('learning-upgrade'))
  const readingKeys = useRef(new Map<string, string>())
  const opened = useRef(false)
  const recordedBlocks = useRef(new Set<string>())
  const [tutorAttempts, setTutorAttempts] = useState<
    Record<string, TutorAttemptScope>
  >({})
  const [upgradeConfirmation, setUpgradeConfirmation] = useState('')
  const [upgradeCreated, setUpgradeCreated] = useState(false)
  const append = appendEvent.mutateAsync

  const updateTutorAttempt = useCallback((attempt: TutorAttemptScope) => {
    setTutorAttempts((current) => {
      const existing = current[attempt.exerciseKey]
      if (
        existing
        && existing.conceptKey === attempt.conceptKey
        && existing.attemptKey === attempt.attemptKey
        && existing.graded === attempt.graded
      ) return current
      return { ...current, [attempt.exerciseKey]: attempt }
    })
  }, [])

  useEffect(() => {
    if (
      !courseId
      || !chapterKey
      || opened.current
      || !overview.data
      || !chapter.data
      || chapter.data.course_version_id !== overview.data.course_version_id
      || chapter.data.snapshot_token !== overview.data.chapters.find(
        (item) => item.chapter_key === chapterKey,
      )?.snapshot_token
    ) return
    opened.current = true
    void append({
      snapshot_token: chapter.data.snapshot_token,
      idempotency_key: openedKey.current,
      chapter_key: chapterKey,
      kind: 'chapter_opened',
      payload: { block_key: null },
    }).catch(() => undefined)
  }, [append, chapter.data, chapterKey, courseId, overview.data])

  const recordPosition = (blockKey: string) => {
    if (!chapter.data) return
    if (recordedBlocks.current.has(blockKey)) return
    recordedBlocks.current.add(blockKey)
    let idempotencyKey = readingKeys.current.get(blockKey)
    if (!idempotencyKey) {
      idempotencyKey = sessionKey('position')
      readingKeys.current.set(blockKey, idempotencyKey)
    }
    void append({
      snapshot_token: chapter.data.snapshot_token,
      idempotency_key: idempotencyKey,
      chapter_key: chapterKey,
      kind: 'reading_position',
      payload: { block_key: blockKey },
    }).catch(() => {
      recordedBlocks.current.delete(blockKey)
    })
  }

  if (course.isLoading || overview.isLoading || chapter.isLoading) {
    return <AppShell><CoursePageLoading /></AppShell>
  }
  if (
    (course.isError && isNotFoundError(course.error))
    || (overview.isError && isNotFoundError(overview.error))
    || (chapter.isError && isNotFoundError(chapter.error))
  ) {
    return (
      <AppShell>
        <div className="flex-1 overflow-y-auto p-6"><CoursePageNotFound /></div>
      </AppShell>
    )
  }
  if (
    course.isError
    || overview.isError
    || chapter.isError
    || !course.data
    || !overview.data
    || !chapter.data
  ) {
    return (
      <AppShell>
        <div className="flex-1 overflow-y-auto p-6">
          <CoursePageError onRetry={() => {
            void course.refetch()
            void overview.refetch()
            void chapter.refetch()
          }} />
        </div>
      </AppShell>
    )
  }

  const overviewChapter = overview.data.chapters.find(
    (item) => item.chapter_key === chapterKey,
  )
  if (
    !overviewChapter
    || chapter.data.course_version_id !== overview.data.course_version_id
    || chapter.data.snapshot_token !== overviewChapter.snapshot_token
  ) {
    return (
      <AppShell>
        <div className="flex-1 overflow-y-auto p-6"><CoursePageNotFound /></div>
      </AppShell>
    )
  }
  const refreshLearningSnapshot = () => {
    void Promise.all([
      overview.refetch(),
      chapter.refetch(),
      sources.refetch(),
      notes.refetch(),
      exercises.refetch(),
      labs.refetch(),
    ])
  }
  const relatedSnapshotChanged = (
    Boolean(sources.data && sources.data.snapshot_token !== chapter.data.snapshot_token)
    || Boolean(notes.data && notes.data.snapshot_token !== chapter.data.snapshot_token)
  )
  const learningExercises = (exercises.data ?? []).filter(
    (exercise) => exercise.learning_blocked_reason !== 'verification_required',
  )
  const needsLearningUpgrade = (
    !exercises.isLoading
    && !exercises.isError
    && (
      (exercises.data ?? []).length === 0
      || !learningExercises.some(
        (exercise) => exercise.is_core && exercise.is_gating,
      )
    )
  )

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl space-y-6 p-6">
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <Button asChild variant="ghost" size="sm" className="-ml-3 mb-2">
                <Link href={`/courses/${encodeURIComponent(courseId)}/learn`}>
                  <ArrowLeft aria-hidden="true" />
                  {t('course.backToLearnOverview')}
                </Link>
              </Button>
              <p className="text-sm font-medium text-primary">{t('course.learnMode')}</p>
              <h1 className="font-display text-3xl font-bold">{overviewChapter.title}</h1>
            </div>
            <Button asChild variant="outline">
              <Link href={`/courses/${encodeURIComponent(courseId)}/chapters/${encodeURIComponent(chapterKey)}`}>
                <Hammer aria-hidden="true" />
                {t('course.openBuildMode')}
              </Link>
            </Button>
          </header>

          <ChapterReader
            artifact={chapter.data.artifact}
            labs={labs.data ?? []}
            onPosition={recordPosition}
          />

          {relatedSnapshotChanged && (
            <Alert variant="destructive">
              <AlertTitle>{t('course.learnContentUnavailable')}</AlertTitle>
              <AlertDescription className="space-y-1">
                <p>{t('course.learningSnapshotChanged')}</p>
                <p>{t('course.learnContentUnavailableDescription')}</p>
              </AlertDescription>
            </Alert>
          )}

          {sources.isLoading ? <CourseInlineLoading /> : sources.isError ? (
            <CourseInlineError onRetry={() => void sources.refetch()} />
          ) : sources.data && sources.data.snapshot_token === chapter.data.snapshot_token ? (
            <LearnerSources courseId={courseId} response={sources.data} />
          ) : null}

          <section aria-labelledby="learn-exercises-title" className="space-y-4">
            <h2 id="learn-exercises-title" className="font-display text-xl font-bold">
              {t('course.exercises')}
            </h2>
            {exercises.isLoading ? <CourseInlineLoading /> : exercises.isError ? (
              <CourseInlineError onRetry={() => void exercises.refetch()} />
            ) : needsLearningUpgrade ? (
              <Alert>
                <AlertTitle>{t('course.learningUpgradeRequired')}</AlertTitle>
                <AlertDescription className="space-y-4">
                  <p>{t('course.learningUpgradeDescription')}</p>
                  {upgradeCreated ? (
                    <div className="space-y-3">
                      <p>{t('course.learningUpgradeCreated')}</p>
                      <Button asChild variant="outline">
                        <Link href={`/courses/${encodeURIComponent(courseId)}/chapters/${encodeURIComponent(chapterKey)}`}>
                          <Hammer aria-hidden="true" />
                          {t('course.openLearningUpgradeBuild')}
                        </Link>
                      </Button>
                    </div>
                  ) : (
                    <div className="max-w-xl space-y-2">
                      <Label htmlFor="learning-upgrade-confirmation">
                        {t('course.learningUpgradeConfirmation')}
                      </Label>
                      <p className="font-mono text-sm">创建学习升级版本</p>
                      <Input
                        id="learning-upgrade-confirmation"
                        value={upgradeConfirmation}
                        placeholder={t('course.learningUpgradeConfirmationHint')}
                        onChange={(event) => setUpgradeConfirmation(event.target.value)}
                        autoComplete="off"
                      />
                      <Button
                        type="button"
                        disabled={
                          upgradeConfirmation !== '创建学习升级版本'
                          || prepareUpgrade.isPending
                        }
                        onClick={() => {
                          void prepareUpgrade.mutateAsync({
                            confirmation: '创建学习升级版本',
                            idempotency_key: upgradeKey.current,
                          }).then(() => setUpgradeCreated(true)).catch(() => undefined)
                        }}
                      >
                        {t('course.createLearningUpgrade')}
                      </Button>
                    </div>
                  )}
                </AlertDescription>
              </Alert>
            ) : learningExercises.map((exercise) => {
              const selectedConcept = selectExerciseConcept(exercise, overview.data)
              if (!selectedConcept) return null
              const mastery = overview.data.masteries.find(
                (item) => item.chapter_key === chapterKey
                  && item.concept_key === selectedConcept,
              )
              const reviewMode = overview.data.review_queue.some(
                (item) => item.chapter_key === chapterKey
                  && item.concept_key === selectedConcept,
              )
              return (
                <ExerciseRunner
                  key={`${exercise.key}:${selectedConcept}:${exercise.snapshot_token}`}
                  courseId={courseId}
                  chapterKey={chapterKey}
                  exercise={exercise}
                  conceptKey={selectedConcept}
                  conceptLabel={conceptLabel(overview.data, selectedConcept)}
                  mastery={mastery}
                  pendingTransfers={overview.data.masteries.flatMap(
                    (item) => (item.pending_transfers ?? []).filter(
                      (pending) => pending.chapter_key === chapterKey
                        && pending.exercise_key === exercise.key,
                    ),
                  )}
                  reviewMode={reviewMode}
                  onStaleSnapshot={refreshLearningSnapshot}
                  onAttemptChange={updateTutorAttempt}
                />
              )
            })}
          </section>

          {!needsLearningUpgrade && (
            <ChapterTutor
              courseId={courseId}
              courseVersionId={chapter.data.course_version_id}
              chapterKey={chapterKey}
              snapshotToken={chapter.data.snapshot_token}
              exercises={learningExercises}
              concepts={overview.data.concepts}
              attempts={Object.values(tutorAttempts).filter((attempt) => (
                learningExercises.some(
                  (exercise) => exercise.key === attempt.exerciseKey,
                )
              ))}
            />
          )}

          {notes.isLoading ? <CourseInlineLoading /> : notes.isError ? (
            <CourseInlineError onRetry={() => void notes.refetch()} />
          ) : notes.data && notes.data.snapshot_token === chapter.data.snapshot_token ? (
            <ChapterNotes
              courseId={courseId}
              chapterKey={chapterKey}
              snapshotToken={chapter.data.snapshot_token}
              artifact={chapter.data.artifact}
              response={notes.data}
            />
          ) : null}
        </div>
      </div>
    </AppShell>
  )
}
