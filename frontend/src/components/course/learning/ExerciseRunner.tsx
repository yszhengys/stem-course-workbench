'use client'

import { useState } from 'react'
import { CheckCircle2, HelpCircle, RotateCcw, ShieldAlert } from 'lucide-react'

import {
  AnswerEditor,
  emptyLearnerAnswer,
  isLearnerAnswerComplete,
} from '@/components/course/learning/AnswerEditor'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useTranslation } from '@/lib/hooks/use-translation'
import {
  exerciseTypeLabel,
  gradeFeedbackLabel,
  masteryReasonLabel,
  masteryStatusLabel,
} from '@/lib/course/mastery'
import {
  useGradeCourseExercise,
  useGradeCourseTransfer,
  useNextCourseExerciseHint,
  useRevealCourseExerciseAnswer,
} from '@/lib/hooks/use-courses'
import type {
  ConceptMastery,
  CourseExercise,
  CourseExerciseGradeResponse,
  CourseExerciseRevealResponse,
} from '@/lib/types/course'

function newStableKey(prefix: string): string {
  const time = Date.now().toString(36)
  const random = Math.random().toString(36).slice(2, 12)
  return `${prefix}-${time}-${random}`.slice(0, 100)
}

function isConflict(error: unknown): boolean {
  if (error === null || typeof error !== 'object' || !('response' in error)) return false
  const response = (error as { response?: unknown }).response
  return response !== null
    && typeof response === 'object'
    && 'status' in response
    && response.status === 409
}

function displayAnswer(answer: unknown): string {
  if (typeof answer === 'string') return answer
  try {
    return JSON.stringify(answer)
  } catch {
    return ''
  }
}

export function ExerciseRunner({
  courseId,
  chapterKey,
  exercise,
  conceptKey,
  conceptLabel,
  mastery,
  reviewMode,
  onStaleSnapshot,
}: {
  courseId: string
  chapterKey: string
  exercise: CourseExercise
  conceptKey: string
  conceptLabel?: string
  mastery?: ConceptMastery
  reviewMode: boolean
  onStaleSnapshot?: () => void
}) {
  const { t } = useTranslation()
  const gradeExercise = useGradeCourseExercise(courseId)
  const gradeTransfer = useGradeCourseTransfer(courseId)
  const nextHint = useNextCourseExerciseHint(courseId)
  const revealAnswer = useRevealCourseExerciseAnswer(courseId)
  const [attemptKey, setAttemptKey] = useState(() => newStableKey('attempt'))
  const [answer, setAnswer] = useState(() => emptyLearnerAnswer(exercise.answer_format))
  const [currentHint, setCurrentHint] = useState<{
    index: number
    total: number
    text: string
  }>()
  const [reveal, setReveal] = useState<CourseExerciseRevealResponse>()
  const [gradeResponse, setGradeResponse] = useState<CourseExerciseGradeResponse>()
  const [transferAnswer, setTransferAnswer] = useState<unknown>()
  const [transferGrade, setTransferGrade] = useState<CourseExerciseGradeResponse>()
  const [actionError, setActionError] = useState<string>()
  const currentMastery = transferGrade?.mastery ?? gradeResponse?.mastery ?? reveal?.mastery ?? mastery
  const isBusy = gradeExercise.isPending
    || gradeTransfer.isPending
    || nextHint.isPending
    || revealAnswer.isPending

  const handleError = (error: unknown) => {
    if (isConflict(error)) {
      setActionError(t('course.learningSnapshotChanged'))
      onStaleSnapshot?.()
      return
    }
    setActionError(error instanceof Error ? error.message : t('course.operationFailed'))
  }

  const requireSameSnapshot = (snapshot: string) => {
    if (snapshot !== exercise.snapshot_token) {
      setActionError(t('course.learningSnapshotChanged'))
      onStaleSnapshot?.()
      return false
    }
    return true
  }

  const showNextHint = async () => {
    const nextIndex = (currentHint?.index ?? 0) + 1
    if (nextIndex > 4) return
    setActionError(undefined)
    try {
      const response = await nextHint.mutateAsync({
        exerciseKey: exercise.key,
        request: {
          snapshot_token: exercise.snapshot_token,
          idempotency_key: `${attemptKey}-hint-${nextIndex}`,
          chapter_key: chapterKey,
          concept_key: conceptKey,
          attempt_key: attemptKey,
          hint_index: nextIndex,
        },
      })
      if (!requireSameSnapshot(response.snapshot_token)) return
      setCurrentHint({
        index: response.hint_index,
        total: response.total_hints,
        text: response.hint,
      })
    } catch (error) {
      handleError(error)
    }
  }

  const confirmReveal = async () => {
    setActionError(undefined)
    try {
      const response = await revealAnswer.mutateAsync({
        exerciseKey: exercise.key,
        request: {
          snapshot_token: exercise.snapshot_token,
          idempotency_key: `${attemptKey}-reveal`,
          chapter_key: chapterKey,
          concept_key: conceptKey,
          attempt_key: attemptKey,
        },
      })
      if (!requireSameSnapshot(response.snapshot_token)) return
      setReveal(response)
      setTransferAnswer(
        response.transfer ? emptyLearnerAnswer(response.transfer.answer_format) : undefined,
      )
    } catch (error) {
      handleError(error)
    }
  }

  const submitAnswer = async () => {
    if (!isLearnerAnswerComplete(exercise.answer_format, answer) || gradeResponse) return
    setActionError(undefined)
    try {
      const response = await gradeExercise.mutateAsync({
        exerciseKey: exercise.key,
        request: {
          snapshot_token: exercise.snapshot_token,
          chapter_key: chapterKey,
          concept_key: conceptKey,
          attempt_key: attemptKey,
          answer,
          hints_used: currentHint?.index ?? 0,
          answer_revealed: Boolean(reveal),
          mode: reviewMode ? 'review' : 'practice',
        },
      })
      if (!requireSameSnapshot(response.snapshot_token)) return
      setGradeResponse(response)
    } catch (error) {
      handleError(error)
    }
  }

  const submitTransfer = async () => {
    if (
      !reveal?.transfer
      || transferAnswer === undefined
      || !isLearnerAnswerComplete(reveal.transfer.answer_format, transferAnswer)
      || transferGrade?.grade.correct === true
    ) return
    setActionError(undefined)
    try {
      const response = await gradeTransfer.mutateAsync({
        exerciseKey: exercise.key,
        request: {
          snapshot_token: exercise.snapshot_token,
          chapter_key: chapterKey,
          concept_key: conceptKey,
          source_attempt_key: attemptKey,
          attempt_key: newStableKey('transfer'),
          transfer_task_key: reveal.transfer.key,
          answer: transferAnswer,
        },
      })
      if (!requireSameSnapshot(response.snapshot_token)) return
      setTransferGrade(response)
    } catch (error) {
      handleError(error)
    }
  }

  const startAnotherAttempt = () => {
    setAttemptKey(newStableKey('attempt'))
    setAnswer(emptyLearnerAnswer(exercise.answer_format))
    setCurrentHint(undefined)
    setReveal(undefined)
    setGradeResponse(undefined)
    setTransferAnswer(undefined)
    setTransferGrade(undefined)
    setActionError(undefined)
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{exercise.prompt}</CardTitle>
            {conceptLabel && <p className="text-sm text-muted-foreground">{conceptLabel}</p>}
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant={exercise.is_core ? 'default' : 'secondary'}>
              {exerciseTypeLabel(t, exercise.exercise_type)}
            </Badge>
            {reviewMode && <Badge variant="secondary">{t('course.reviewAttempt')}</Badge>}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {exercise.source_number && (
          <p className="text-xs text-muted-foreground">
            {t('course.sourceExerciseNumber', { number: exercise.source_number })}
          </p>
        )}

        <div className="space-y-3" aria-live="polite">
          {currentHint && (
            <Alert>
              <HelpCircle aria-hidden="true" />
              <AlertTitle>
                {t('course.hintProgress', {
                  current: currentHint.index,
                  total: currentHint.total,
                })}
              </AlertTitle>
              <AlertDescription>{currentHint.text}</AlertDescription>
            </Alert>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void showNextHint()}
            disabled={
              isBusy
              || (currentHint !== undefined && currentHint.index >= currentHint.total)
              || Boolean(gradeResponse)
            }
          >
            {currentHint && currentHint.index >= currentHint.total
              ? t('course.allHintsViewed')
              : t('course.nextHint')}
          </Button>
        </div>

        <div className="space-y-3">
          <AnswerEditor
            format={exercise.answer_format}
            value={answer}
            onChange={setAnswer}
            disabled={Boolean(gradeResponse)}
          />
          <div className="flex flex-wrap gap-3">
            <Button
              type="button"
              onClick={() => void submitAnswer()}
              disabled={
                isBusy
                || !isLearnerAnswerComplete(exercise.answer_format, answer)
                || Boolean(gradeResponse)
              }
            >
              <CheckCircle2 aria-hidden="true" />
              {t('course.checkAnswer')}
            </Button>

            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  disabled={isBusy || Boolean(reveal) || Boolean(gradeResponse)}
                >
                  {t('course.revealAnswer')}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>{t('course.confirmRevealTitle')}</AlertDialogTitle>
                  <AlertDialogDescription>
                    {t('course.confirmRevealDescription')}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                  <AlertDialogAction onClick={() => void confirmReveal()}>
                    {t('course.confirmReveal')}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>

        {actionError && (
          <Alert variant="destructive">
            <ShieldAlert aria-hidden="true" />
            <AlertTitle>{t('course.operationFailed')}</AlertTitle>
            <AlertDescription>{actionError}</AlertDescription>
          </Alert>
        )}

        {reveal && (
          <Alert>
            <AlertTitle>{t('course.answerRevealed')}</AlertTitle>
            <AlertDescription className="space-y-3">
              <p>{displayAnswer(reveal.answer)}</p>
              {reveal.transfer && transferAnswer !== undefined && (
                <div className="space-y-3 rounded-md border p-3">
                  <p className="font-semibold">{t('course.transferTask')}</p>
                  <p>{reveal.transfer.prompt}</p>
                  <AnswerEditor
                    format={reveal.transfer.answer_format}
                    value={transferAnswer}
                    onChange={setTransferAnswer}
                    disabled={transferGrade?.grade.correct === true}
                  />
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => void submitTransfer()}
                    disabled={
                      isBusy
                      || !isLearnerAnswerComplete(reveal.transfer.answer_format, transferAnswer)
                      || transferGrade?.grade.correct === true
                    }
                  >
                    {t('course.checkTransferAnswer')}
                  </Button>
                  {transferGrade && (
                    <p aria-live="polite" className="font-medium">
                      {transferGrade.grade.correct === true
                        ? t('course.transferCompleted')
                        : gradeFeedbackLabel(t, transferGrade.grade)}
                    </p>
                  )}
                </div>
              )}
            </AlertDescription>
          </Alert>
        )}

        {gradeResponse && (
          <Alert variant={gradeResponse.grade.correct === false ? 'destructive' : 'default'}>
            <AlertTitle>{gradeFeedbackLabel(t, gradeResponse.grade)}</AlertTitle>
            <AlertDescription className="space-y-2">
              {currentMastery ? (
                <>
                  <p>
                    {t('course.currentMastery')}: {masteryStatusLabel(t, currentMastery.status)}
                  </p>
                  <p>{masteryReasonLabel(t, currentMastery)}</p>
                </>
              ) : (
                <p>{t('course.masteryNotChanged')}</p>
              )}
              <Button type="button" variant="outline" size="sm" onClick={startAnotherAttempt}>
                <RotateCcw aria-hidden="true" />
                {t('course.tryAnotherAttempt')}
              </Button>
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  )
}
