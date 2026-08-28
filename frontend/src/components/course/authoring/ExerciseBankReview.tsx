'use client'

import { useState } from 'react'
import { BookCheck, ShieldCheck } from 'lucide-react'

import { CommandJobPanel } from '@/components/course/CommandJobPanel'
import { CourseModelPicker } from '@/components/course/CourseModelPicker'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Textarea } from '@/components/ui/textarea'
import { useTranslation } from '@/lib/hooks/use-translation'
import type {
  CourseExerciseBuildStatus,
  CourseExerciseVerificationRequest,
  CourseFinding,
  CourseModelOption,
  EvidenceAnchor,
  ModelSelection,
} from '@/lib/types/course'

interface ExerciseBankReviewProps {
  status: CourseExerciseBuildStatus | undefined
  anchors: EvidenceAnchor[]
  findings: CourseFinding[]
  options: CourseModelOption[]
  generationModel: ModelSelection | null
  reviewModel: ModelSelection | null
  onGenerationModelChange: (selection: ModelSelection | null) => void
  onReviewModelChange: (selection: ModelSelection | null) => void
  canGenerate: boolean
  isGenerating: boolean
  isVerifying: boolean
  onGenerate: () => void
  onVerify: (
    exerciseKey: string,
    request: CourseExerciseVerificationRequest,
  ) => void | Promise<void>
  onRetry: () => void
}

function verificationLabel(
  t: ReturnType<typeof useTranslation>['t'],
  level: 'L0' | 'L1' | 'L2' | 'L3',
) {
  return {
    L0: t('course.verificationL0'),
    L1: t('course.verificationL1'),
    L2: t('course.verificationL2'),
    L3: t('course.verificationL3'),
  }[level]
}

function displayJson(value: unknown): string {
  return JSON.stringify(value, null, 2) ?? 'null'
}

export function ExerciseBankReview({
  status,
  anchors,
  findings,
  options,
  generationModel,
  reviewModel,
  onGenerationModelChange,
  onReviewModelChange,
  canGenerate,
  isGenerating,
  isVerifying,
  onGenerate,
  onVerify,
  onRetry,
}: ExerciseBankReviewProps) {
  const { t } = useTranslation()
  const [reasons, setReasons] = useState<Record<string, string>>({})
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({})

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookCheck className="size-5 text-fern" />
          {t('course.exerciseBankTitle')}
        </CardTitle>
        <CardDescription>{t('course.exerciseBankDescription')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="space-y-2">
            <h3 className="font-medium">{t('course.exerciseGenerationModel')}</h3>
            <CourseModelPicker
              idPrefix="course-exercise-generation"
              accessibleLabel={t('course.exerciseGenerationModel')}
              options={options}
              value={generationModel}
              onChange={onGenerationModelChange}
              disabled={isGenerating}
            />
          </div>
          <div className="space-y-2">
            <h3 className="font-medium">{t('course.exerciseReviewModel')}</h3>
            <CourseModelPicker
              idPrefix="course-exercise-review"
              accessibleLabel={t('course.exerciseReviewModel')}
              options={options}
              value={reviewModel}
              onChange={onReviewModelChange}
              disabled={isGenerating}
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            onClick={onGenerate}
            disabled={!canGenerate || isGenerating || !generationModel || !reviewModel}
          >
            {status?.status === 'succeeded'
              ? t('course.regenerateExerciseBank')
              : t('course.generateExerciseBank')}
          </Button>
          {status?.status === 'failed' && (
            <Button variant="outline" onClick={onRetry}>
              {t('common.retry')}
            </Button>
          )}
        </div>

        <CommandJobPanel
          status={status?.status === 'not_started' ? undefined : status?.status}
          errorMessage={status?.error_message}
        />

        {status?.status === 'succeeded' && status.exercises.length === 0 && (
          <Alert variant="destructive">
            <AlertTitle>{t('course.exerciseBankUnavailable')}</AlertTitle>
            <AlertDescription>{t('course.exerciseBankUnavailableDescription')}</AlertDescription>
          </Alert>
        )}

        <div className="space-y-5">
          {(status?.exercises ?? []).map((exercise) => {
            const evidence = exercise.blueprint.source_anchor_ids
              .map((anchorId) => anchors.find((anchor) => anchor.anchor_id === anchorId))
              .filter((anchor): anchor is EvidenceAnchor => anchor !== undefined)
            const exerciseFindings = findings.filter(
              (record) => record.finding.item_key === exercise.key,
            )
            const reason = reasons[exercise.key] ?? ''
            const isConfirmed = confirmed[exercise.key] ?? false
            const isVerified = exercise.verification.level === 'L3'

            return (
              <article key={exercise.key} className="space-y-4 rounded-lg border p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="font-medium">{exercise.blueprint.prompt}</h3>
                    <p className="mt-1 font-mono text-xs text-muted-foreground">{exercise.key}</p>
                  </div>
                  <Badge variant={exercise.verification.level === 'L1' ? 'secondary' : 'outline'}>
                    {verificationLabel(t, exercise.verification.level)}
                  </Badge>
                </div>

                <div className="grid gap-4 lg:grid-cols-2">
                  <div>
                    <p className="mb-2 text-sm font-medium">{t('course.expectedAnswer')}</p>
                    <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">
                      {displayJson(exercise.expected_answer)}
                    </pre>
                  </div>
                  <div>
                    <p className="mb-2 text-sm font-medium">{t('course.graderSpec')}</p>
                    <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">
                      {displayJson(exercise.blueprint.grader)}
                    </pre>
                  </div>
                </div>

                {exercise.blueprint.hints.length > 0 && (
                  <div>
                    <p className="mb-2 text-sm font-medium">{t('course.progressiveHints')}</p>
                    <ol className="list-decimal space-y-1 pl-5 text-sm">
                      {exercise.blueprint.hints.map((hint) => <li key={hint}>{hint}</li>)}
                    </ol>
                  </div>
                )}

                <div>
                  <p className="mb-2 text-sm font-medium">{t('course.exerciseEvidence')}</p>
                  {evidence.map((anchor) => (
                    <blockquote key={anchor.anchor_id} className="mb-2 border-l-2 pl-3 text-sm text-muted-foreground">
                      {anchor.locator.quote}
                    </blockquote>
                  ))}
                </div>

                {exercise.blueprint.transfer_task && (
                  <div className="rounded border bg-muted/30 p-3 text-sm">
                    <p className="font-medium">{t('course.transferTask')}</p>
                    <p className="mt-1">{exercise.blueprint.transfer_task.prompt}</p>
                  </div>
                )}

                {exercise.review_run_ids.length > 0 && (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <ShieldCheck className="size-4" />{t('course.exerciseReviewRecorded')}
                  </p>
                )}
                {exerciseFindings.map((record) => (
                  <Alert key={record.id} variant={record.severity === 'high' || record.severity === 'error' ? 'destructive' : 'default'}>
                    <AlertDescription>{record.finding.message}</AlertDescription>
                  </Alert>
                ))}

                {!isVerified && (
                  <div className="space-y-3 border-t pt-4">
                    <label className="flex items-start gap-3 text-sm">
                      <Checkbox
                        checked={isConfirmed}
                        aria-label={t('course.confirmExpectedAnswer')}
                        onCheckedChange={(value) => setConfirmed((current) => ({
                          ...current,
                          [exercise.key]: value === true,
                        }))}
                      />
                      <span>{t('course.confirmExpectedAnswer')}</span>
                    </label>
                    <Textarea
                      aria-label={t('course.verificationReason')}
                      value={reason}
                      onChange={(event) => setReasons((current) => ({
                        ...current,
                        [exercise.key]: event.target.value,
                      }))}
                      placeholder={t('course.verificationReasonPlaceholder')}
                    />
                    <Button
                      variant="outline"
                      disabled={!isConfirmed || !reason.trim() || isVerifying}
                      onClick={() => void onVerify(exercise.key, {
                        snapshot_token: exercise.snapshot_token,
                        expected_answer_confirmation: exercise.expected_answer,
                        reason: reason.trim(),
                      })}
                    >
                      {t('course.verifyExercise')}
                    </Button>
                  </div>
                )}
              </article>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}
