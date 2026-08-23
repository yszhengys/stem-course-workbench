'use client'

import { useEffect, useMemo, useState } from 'react'
import { Bot, Send } from 'lucide-react'

import { CourseModelPicker } from '@/components/course/CourseModelPicker'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  useCourseModelOptions,
  useCourseTutorSessions,
  useCreateCourseTutorSession,
  useSendCourseTutorMessage,
} from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'
import type {
  CourseExercise,
  CourseTutorMessageRequest,
  ModelSelection,
} from '@/lib/types/course'

type TutorIntent = CourseTutorMessageRequest['intent']

function tutorAttemptKey(): string {
  return `tutor-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 18)}`
    .slice(0, 100)
}

export function ChapterTutor({
  courseId,
  courseVersionId,
  chapterKey,
  snapshotToken,
  exercises,
  concepts,
}: {
  courseId: string
  courseVersionId: string
  chapterKey: string
  snapshotToken: string
  exercises: CourseExercise[]
  concepts: { key: string; label: string }[]
}) {
  const { t } = useTranslation()
  const sessions = useCourseTutorSessions(courseId)
  const models = useCourseModelOptions()
  const createSession = useCreateCourseTutorSession(courseId)
  const [selectedSessionId, setSelectedSessionId] = useState<string>()
  const [selectedModel, setSelectedModel] = useState<ModelSelection | null>(null)
  const [intent, setIntent] = useState<TutorIntent>('explain')
  const [message, setMessage] = useState('')
  const [revealExerciseKey, setRevealExerciseKey] = useState('')
  const [revealConceptKey, setRevealConceptKey] = useState('')
  const [confirmReveal, setConfirmReveal] = useState(false)

  const chapterSessions = useMemo(
    () => (sessions.data ?? []).filter((session) => session.chapter_key === chapterKey),
    [chapterKey, sessions.data],
  )
  useEffect(() => {
    if (chapterSessions.some((session) => session.session_id === selectedSessionId)) return
    const current = chapterSessions.find((session) => (
      session.status === 'active' && session.course_version_id === courseVersionId
    ))
    setSelectedSessionId((current ?? chapterSessions[0])?.session_id)
  }, [chapterSessions, courseVersionId, selectedSessionId])

  const selectedSession = chapterSessions.find(
    (session) => session.session_id === selectedSessionId,
  )
  const writable = Boolean(
    selectedSession
    && selectedSession.status === 'active'
    && selectedSession.course_version_id === courseVersionId,
  )
  const sendMessage = useSendCourseTutorMessage(courseId, selectedSessionId)
  const revealExercises = exercises.filter(
    (exercise) => exercise.is_core && exercise.transfer !== null,
  )
  const revealExercise = revealExercises.find(
    (exercise) => exercise.key === revealExerciseKey,
  )
  const conceptLabel = (key: string) => (
    concepts.find((concept) => concept.key === key)?.label
    ?? t('course.conceptLabelUnavailable')
  )

  const startSession = async () => {
    if (!selectedModel) return
    const created = await createSession.mutateAsync({
      snapshot_token: snapshotToken,
      chapter_key: chapterKey,
      model: selectedModel,
    })
    setSelectedSessionId(created.session_id)
  }

  const submit = async () => {
    const trimmed = message.trim()
    if (!selectedSessionId || !writable || !trimmed) return
    const request: CourseTutorMessageRequest = {
      snapshot_token: snapshotToken,
      content: trimmed,
      intent,
    }
    if (intent === 'reveal') {
      if (!revealExercise || !revealConceptKey || !confirmReveal) return
      request.exercise_key = revealExercise.key
      request.concept_key = revealConceptKey
      request.attempt_key = tutorAttemptKey()
    }
    await sendMessage.mutateAsync(request)
    setMessage('')
    setConfirmReveal(false)
  }

  const revealReady = intent !== 'reveal' || Boolean(
    revealExercise && revealConceptKey && confirmReveal,
  )
  const sendDisabled = (
    !writable
    || !message.trim()
    || !revealReady
    || sendMessage.isPending
  )

  return (
    <section aria-labelledby="chapter-tutor-title" className="space-y-4">
      <div className="flex items-center gap-2">
        <Bot className="size-5" aria-hidden="true" />
        <h2 id="chapter-tutor-title" className="font-display text-xl font-bold">
          {t('course.chapterTutor')}
        </h2>
      </div>
      <p className="text-sm text-muted-foreground">
        {t('course.chapterTutorDescription')}
      </p>

      {sessions.isError && (
        <Alert variant="destructive">
          <AlertTitle>{t('course.tutorLoadFailed')}</AlertTitle>
          <AlertDescription>
            <Button type="button" variant="outline" onClick={() => void sessions.refetch()}>
              {t('common.retry')}
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {chapterSessions.length > 0 && (
        <div className="space-y-2">
          <Label htmlFor="course-tutor-session">{t('course.tutorSession')}</Label>
          <select
            id="course-tutor-session"
            value={selectedSessionId ?? ''}
            onChange={(event) => setSelectedSessionId(event.target.value)}
            className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
          >
            {chapterSessions.map((session) => (
              <option key={session.session_id} value={session.session_id}>
                {session.model.model} — {t(
                  session.status === 'stale'
                    ? 'course.tutorSessionStale'
                    : session.status === 'closed'
                      ? 'course.tutorSessionClosed'
                      : 'course.tutorSessionActive',
                )}
              </option>
            ))}
          </select>
        </div>
      )}

      {selectedSession && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t('course.tutorConversation')}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3" aria-live="polite">
            {selectedSession.turns.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t('course.noTutorMessages')}</p>
            ) : selectedSession.turns.map((turn) => (
              <article
                key={turn.turn_no}
                className="rounded-md border p-3"
                aria-label={turn.role === 'assistant' ? t('course.tutorRole') : t('course.learnerRole')}
              >
                <p className="mb-2 text-xs font-medium text-muted-foreground">
                  {turn.role === 'assistant' ? t('course.tutorRole') : t('course.learnerRole')}
                </p>
                <p className="whitespace-pre-wrap text-sm">{turn.content}</p>
                {turn.anchor_ids.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2" aria-label={t('course.tutorCitations')}>
                    {turn.anchor_ids.map((anchorId, index) => (
                      <Badge key={anchorId} variant="outline" title={anchorId}>
                        {t('course.tutorCitation', { index: index + 1 })}
                      </Badge>
                    ))}
                  </div>
                )}
              </article>
            ))}
          </CardContent>
        </Card>
      )}

      {!writable && selectedSession && (
        <Alert>
          <AlertTitle>{t('course.tutorSessionReadOnly')}</AlertTitle>
          <AlertDescription>{t('course.tutorSessionReadOnlyDescription')}</AlertDescription>
        </Alert>
      )}

      {!writable && (
        <Card>
          <CardHeader><CardTitle className="text-base">{t('course.startTutorSession')}</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <CourseModelPicker
              idPrefix="course-tutor"
              accessibleLabel={t('course.tutorModel')}
              options={models.data?.options ?? []}
              value={selectedModel}
              onChange={setSelectedModel}
              disabled={models.isFetching || createSession.isPending}
            />
            <Button
              type="button"
              onClick={() => void startSession()}
              disabled={!selectedModel || createSession.isPending}
            >
              {t('course.startTutorSession')}
            </Button>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle className="text-base">{t('course.askTutor')}</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="course-tutor-intent">{t('course.tutorIntent')}</Label>
            <select
              id="course-tutor-intent"
              aria-label={t('course.tutorIntent')}
              value={intent}
              onChange={(event) => {
                setIntent(event.target.value as TutorIntent)
                setConfirmReveal(false)
              }}
              disabled={!writable || sendMessage.isPending}
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
            >
              <option value="explain">{t('course.tutorIntentExplain')}</option>
              <option value="diagnose">{t('course.tutorIntentDiagnose')}</option>
              <option value="hint">{t('course.tutorIntentHint')}</option>
              <option value="reveal">{t('course.tutorIntentReveal')}</option>
            </select>
          </div>

          {intent === 'reveal' && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="course-tutor-reveal-exercise">
                  {t('course.tutorRevealExercise')}
                </Label>
                <select
                  id="course-tutor-reveal-exercise"
                  aria-label={t('course.tutorRevealExercise')}
                  value={revealExerciseKey}
                  onChange={(event) => {
                    setRevealExerciseKey(event.target.value)
                    setRevealConceptKey('')
                    setConfirmReveal(false)
                  }}
                  disabled={!writable}
                  className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
                >
                  <option value="">{t('course.selectTutorRevealExercise')}</option>
                  {revealExercises.map((item) => (
                    <option key={item.key} value={item.key}>{item.prompt}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="course-tutor-reveal-concept">
                  {t('course.tutorRevealConcept')}
                </Label>
                <select
                  id="course-tutor-reveal-concept"
                  aria-label={t('course.tutorRevealConcept')}
                  value={revealConceptKey}
                  onChange={(event) => setRevealConceptKey(event.target.value)}
                  disabled={!writable || !revealExercise}
                  className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
                >
                  <option value="">{t('course.selectTutorRevealConcept')}</option>
                  {(revealExercise?.concept_keys ?? []).map((key) => (
                    <option key={key} value={key}>{conceptLabel(key)}</option>
                  ))}
                </select>
              </div>
              <label className="flex items-start gap-2 text-sm sm:col-span-2">
                <input
                  type="checkbox"
                  checked={confirmReveal}
                  onChange={(event) => setConfirmReveal(event.target.checked)}
                  disabled={!writable}
                  aria-label={t('course.confirmTutorReveal')}
                />
                <span>{t('course.confirmTutorReveal')}</span>
              </label>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="course-tutor-message">{t('course.tutorMessage')}</Label>
            <Textarea
              id="course-tutor-message"
              aria-label={t('course.tutorMessage')}
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              disabled={!writable || sendMessage.isPending}
              placeholder={t('course.tutorMessagePlaceholder')}
              maxLength={20_000}
            />
          </div>
          <Button type="button" onClick={() => void submit()} disabled={sendDisabled}>
            <Send aria-hidden="true" />
            {t('course.sendTutorMessage')}
          </Button>
          <p className="text-xs text-muted-foreground">
            {t('course.tutorGroundingNotice')}
          </p>
        </CardContent>
      </Card>
    </section>
  )
}
