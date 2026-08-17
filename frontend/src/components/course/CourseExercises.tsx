'use client'

import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { ChapterArtifact, CreateCourseAttemptRequest } from '@/lib/types/course'

type Exercise = ChapterArtifact['exercises'][number]

export function CourseExercises({
  exercises,
  persistentLabKey,
  disabled,
  onSave,
}: {
  exercises: Exercise[]
  persistentLabKey: string | undefined
  disabled: boolean
  onSave: (submission: { labKey: string; request: CreateCourseAttemptRequest }) => void
}) {
  const { t } = useTranslation()
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [hintsVisible, setHintsVisible] = useState<Set<string>>(new Set())
  const [answersRevealed, setAnswersRevealed] = useState<Set<string>>(new Set())
  const [transferCompleted, setTransferCompleted] = useState<Set<string>>(new Set())

  const toggleSet = (
    setter: React.Dispatch<React.SetStateAction<Set<string>>>,
    key: string,
    enabled?: boolean,
  ) => setter((current) => {
    const next = new Set(current)
    const shouldEnable = enabled ?? !next.has(key)
    if (shouldEnable) next.add(key)
    else next.delete(key)
    return next
  })

  return (
    <div className="space-y-4">
      {!persistentLabKey && exercises.length > 0 && (
        <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm text-muted-foreground">
          {t('course.exercisePersistenceUnavailable')}
        </p>
      )}
      {exercises.map((exercise) => {
        const showingHints = hintsVisible.has(exercise.key)
        const revealed = answersRevealed.has(exercise.key)
        const transferred = transferCompleted.has(exercise.key)
        const answer = answers[exercise.key] ?? ''
        return (
          <div key={exercise.key} className="space-y-3 rounded-md border p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="font-medium">{exercise.prompt}</p>
              <Badge variant="secondary">{exercise.difficulty}</Badge>
            </div>

            {exercise.hints.length > 0 && (
              <div>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => toggleSet(setHintsVisible, exercise.key)}
                >
                  {showingHints ? t('course.hideHints') : t('course.viewHints')}
                </Button>
                {showingHints && (
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                    {exercise.hints.map((hint) => <li key={hint}>{hint}</li>)}
                  </ul>
                )}
              </div>
            )}

            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                aria-label={t('course.exerciseAnswer')}
                value={answer}
                onChange={(event) => setAnswers((current) => ({
                  ...current,
                  [exercise.key]: event.target.value,
                }))}
                placeholder={t('course.exerciseAnswer')}
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => toggleSet(setAnswersRevealed, exercise.key, true)}
              >
                {t('course.revealAnswer')}
              </Button>
            </div>

            {revealed && (
              <div className="space-y-3 rounded-md bg-muted/50 p-3 text-sm">
                <p><strong>{t('course.answer')}:</strong> {exercise.answer}</p>
                <p><strong>{t('course.transferTask')}:</strong> {exercise.transfer_task}</p>
                <label className="flex items-center gap-2">
                  <Checkbox
                    aria-label={t('course.transferComplete')}
                    checked={transferred}
                    onCheckedChange={(checked) => toggleSet(
                      setTransferCompleted,
                      exercise.key,
                      checked === true,
                    )}
                  />
                  <span>{t('course.transferComplete')}</span>
                </label>
              </div>
            )}

            <Button
              type="button"
              size="sm"
              disabled={disabled || !persistentLabKey || !answer.trim()}
              onClick={() => {
                if (!persistentLabKey) return
                onSave({
                  labKey: persistentLabKey,
                  request: {
                    answers: { answer: answer.trim() },
                    exercise_key: exercise.key,
                    answer: answer.trim(),
                    hints_used: showingHints ? exercise.hints.length : 0,
                    answer_revealed: revealed,
                    transfer_completed: transferred,
                  },
                })
              }}
            >
              {t('course.saveExerciseAttempt')}
            </Button>
          </div>
        )
      })}
    </div>
  )
}
