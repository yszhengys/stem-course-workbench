'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft, Beaker, BookCheck, FileWarning, NotebookPen } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { ExerciseBankReview } from '@/components/course/authoring/ExerciseBankReview'
import { StructuredDraftEditor } from '@/components/course/authoring/StructuredDraftEditor'
import { ChapterPublicationGate } from '@/components/course/ChapterPublicationGate'
import { CommandJobPanel } from '@/components/course/CommandJobPanel'
import { CourseModelPicker } from '@/components/course/CourseModelPicker'
import { CourseExercises } from '@/components/course/CourseExercises'
import { EvidenceAnchorCard } from '@/components/course/EvidenceAnchorCard'
import {
  CourseInlineError,
  CourseInlineLoading,
  CoursePageError,
  CoursePageLoading,
  CoursePageNotFound,
} from '@/components/course/CoursePageState'
import { LabRenderer } from '@/components/course/LabRenderer'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { MarkdownRenderer } from '@/components/ui/markdown-renderer'
import { Textarea } from '@/components/ui/textarea'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { useCommandStatus } from '@/lib/hooks/use-command-status'
import {
  useCourse,
  useCourseAnchors,
  useCourseAttempts,
  useCourseExerciseBuildStatus,
  useCourseFindings,
  useCourseLabs,
  useCourseModelOptions,
  useCourseNotes,
  useCourseProgress,
  useCreateCourseAttempt,
  useCreateCourseNote,
  useCurrentCourseChapter,
  useCurrentCourseOutline,
  useGenerateCourseChapter,
  useGenerateCourseExerciseBank,
  usePublishCourseChapter,
  useReattachCourseNote,
  useReviewCourseChapter,
  useUpdateCourseFinding,
  useUpdateCourseProgress,
  useVerifyCourseExercise,
} from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'
import {
  canTransitionChapterProgress,
  nextChapterProgressStatus,
  selectChapterNotes,
} from '@/lib/course/chapter-workflow'
import {
  courseStatusLabel,
  provenanceLabel,
} from '@/lib/course/course-labels'
import { selectableDefaultModel } from '@/lib/course/model-selection'
import { canRequestChapterReview } from '@/lib/course/review-policy'
import { isNotFoundError } from '@/lib/utils/error-handler'
import type { ModelSelection } from '@/lib/types/course'

export default function CourseChapterPage() {
  const { t } = useTranslation()
  const params = useParams()
  const courseId = params?.courseId ? decodeURIComponent(params.courseId as string) : ''
  const chapterKey = params?.chapterKey ? decodeURIComponent(params.chapterKey as string) : ''
  const course = useCourse(courseId)
  const outline = useCurrentCourseOutline(courseId, Boolean(course.data?.outline_version_id))
  const chapter = useCurrentCourseChapter(courseId, chapterKey, Boolean(outline.data?.approved_at))
  const anchors = useCourseAnchors(courseId)
  const models = useCourseModelOptions()
  const findings = useCourseFindings(courseId, chapterKey)
  const notes = useCourseNotes(courseId)
  const progress = useCourseProgress(courseId)
  const labs = useCourseLabs(courseId, chapterKey, Boolean(chapter.data?.artifact))
  const attempts = useCourseAttempts(courseId, chapterKey, Boolean(chapter.data?.artifact))
  const exerciseBuild = useCourseExerciseBuildStatus(
    courseId,
    chapterKey,
    Boolean(chapter.data?.artifact),
  )
  const generateChapter = useGenerateCourseChapter(courseId, chapterKey)
  const generateExerciseBank = useGenerateCourseExerciseBank(courseId, chapterKey)
  const verifyExercise = useVerifyCourseExercise(courseId, chapterKey)
  const reviewChapter = useReviewCourseChapter(courseId, chapterKey)
  const updateFinding = useUpdateCourseFinding(courseId, chapterKey)
  const updateProgress = useUpdateCourseProgress(courseId)
  const createNote = useCreateCourseNote(courseId)
  const reattachNote = useReattachCourseNote(courseId)
  const createAttempt = useCreateCourseAttempt(courseId, chapterKey)
  const publishChapter = usePublishCourseChapter(courseId, chapterKey)

  const [contentModel, setContentModel] = useState<ModelSelection | null>(null)
  const [reviewModel, setReviewModel] = useState<ModelSelection | null>(null)
  const [escalationModel, setEscalationModel] = useState<ModelSelection | null>(null)
  const [exerciseGenerationModel, setExerciseGenerationModel] = useState<ModelSelection | null>(null)
  const [exerciseReviewModel, setExerciseReviewModel] = useState<ModelSelection | null>(null)
  const [selectedAnchorIds, setSelectedAnchorIds] = useState<string[]>([])
  const [generationCommandId, setGenerationCommandId] = useState<string>()
  const [reviewCommandId, setReviewCommandId] = useState<string>()
  const [exerciseCommandId, setExerciseCommandId] = useState<string>()
  const [noteContent, setNoteContent] = useState('')
  const [noteBlockKey, setNoteBlockKey] = useState('')
  const [reattachBlocks, setReattachBlocks] = useState<Record<string, string>>({})
  const [attemptAnswers, setAttemptAnswers] = useState<Record<string, string>>({})
  const modelSelectionsInitialized = useRef(false)

  const generationStatus = useCommandStatus(generationCommandId, [
    QUERY_KEYS.course(courseId),
    QUERY_KEYS.courseChapter(courseId, chapterKey),
    QUERY_KEYS.courseLabs(courseId, chapterKey),
  ])
  const reviewStatus = useCommandStatus(reviewCommandId, [
    QUERY_KEYS.course(courseId),
    QUERY_KEYS.courseChapter(courseId, chapterKey),
    QUERY_KEYS.courseFindings(courseId, chapterKey),
  ])
  useCommandStatus(exerciseCommandId, [
    QUERY_KEYS.courseExerciseBuild(courseId, chapterKey),
    QUERY_KEYS.courseFindings(courseId, chapterKey),
  ])

  const outlineChapter = outline.data?.outline_artifact?.chapters.find((item) => item.key === chapterKey)
  const currentChapter = chapter.data
  const artifact = currentChapter?.artifact
  const reviewAllowed = canRequestChapterReview(currentChapter)
  const requiresNewVersion = currentChapter?.status === 'ready' || currentChapter?.status === 'published'
  const selectedContentModel = models.data && contentModel
    ? selectableDefaultModel(models.data.options, contentModel)
    : null
  const selectedReviewModel = models.data && reviewModel
    ? selectableDefaultModel(models.data.options, reviewModel)
    : null
  const selectedEscalationModel = models.data && escalationModel
    ? selectableDefaultModel(models.data.options, escalationModel)
    : null
  const selectedExerciseGenerationModel = models.data && exerciseGenerationModel
    ? selectableDefaultModel(models.data.options, exerciseGenerationModel)
    : null
  const selectedExerciseReviewModel = models.data && exerciseReviewModel
    ? selectableDefaultModel(models.data.options, exerciseReviewModel)
    : null
  const exerciseBankReady = useMemo(() => {
    if (exerciseBuild.data?.status !== 'succeeded' || exerciseBuild.data.exercises.length === 0) {
      return false
    }
    const cores = exerciseBuild.data.exercises.filter((exercise) => exercise.blueprint.is_core)
    const gates = exerciseBuild.data.exercises.filter((exercise) => exercise.blueprint.is_gating)
    if (cores.length !== 1 || gates.length !== 1 || cores[0].key !== gates[0].key) return false
    const core = cores[0]
    return core.blueprint.grader.kind !== 'advisory'
      && core.blueprint.answer_type !== 'proof'
      && core.blueprint.answer_type !== 'explanation'
      && core.blueprint.transfer_task !== null
      && (core.verification.level === 'L2' || core.verification.level === 'L3')
  }, [exerciseBuild.data])
  const blockKeys = useMemo(() => artifact ? [
    ...artifact.sections.map((item) => item.key),
    ...artifact.formulas.map((item) => item.key),
    ...artifact.worked_examples.map((item) => item.key),
    ...artifact.labs.map((item) => item.key),
    ...artifact.exercises.map((item) => item.key),
  ] : [], [artifact])

  useEffect(() => {
    if (!models.data) return
    if (!modelSelectionsInitialized.current) {
      modelSelectionsInitialized.current = true
      setContentModel(selectableDefaultModel(models.data.options, models.data.defaults.chapter_content))
      setReviewModel(selectableDefaultModel(models.data.options, models.data.defaults.review))
      setEscalationModel(selectableDefaultModel(models.data.options, models.data.defaults.escalation))
      setExerciseGenerationModel(selectableDefaultModel(models.data.options, models.data.defaults.exercise_bank))
      setExerciseReviewModel(selectableDefaultModel(models.data.options, models.data.defaults.exercise_bank_review))
      return
    }
    setContentModel((current) => current
      ? selectableDefaultModel(models.data.options, current)
      : null
    )
    setReviewModel((current) => current
      ? selectableDefaultModel(models.data.options, current)
      : null
    )
    setEscalationModel((current) => current
      ? selectableDefaultModel(models.data.options, current)
      : null
    )
    setExerciseGenerationModel((current) => current
      ? selectableDefaultModel(models.data.options, current)
      : null
    )
    setExerciseReviewModel((current) => current
      ? selectableDefaultModel(models.data.options, current)
      : null
    )
  }, [models.data])

  useEffect(() => {
    if (!anchors.data) return
    const preferred = new Set(outlineChapter?.anchor_ids ?? [])
    const available = anchors.data.map((anchor) => anchor.anchor_id)
    const initial = preferred.size ? available.filter((anchorId) => preferred.has(anchorId)) : available
    setSelectedAnchorIds((previous) => previous.length ? previous.filter((item) => available.includes(item)) : initial)
  }, [anchors.data, outlineChapter?.anchor_ids])

  useEffect(() => {
    if (!noteBlockKey && blockKeys.length) setNoteBlockKey(blockKeys[0])
  }, [blockKeys, noteBlockKey])

  const generate = async () => {
    if (!selectedContentModel || selectedAnchorIds.length === 0) return
    const job = await generateChapter.mutateAsync({
      anchor_ids: selectedAnchorIds,
      prompt_version: 'v1',
      model: selectedContentModel,
      force: requiresNewVersion,
    })
    setGenerationCommandId(job.command_id)
  }

  const review = async () => {
    if (!selectedReviewModel || !selectedEscalationModel || selectedAnchorIds.length === 0) return
    const job = await reviewChapter.mutateAsync({
      anchor_ids: selectedAnchorIds,
      prompt_version: 'v1',
      model: selectedReviewModel,
      escalation_model: selectedEscalationModel,
      force: false,
    })
    setReviewCommandId(job.command_id)
  }

  const generateExercises = async () => {
    if (!selectedExerciseGenerationModel || !selectedExerciseReviewModel || selectedAnchorIds.length === 0) {
      return
    }
    const job = await generateExerciseBank.mutateAsync({
      anchor_ids: selectedAnchorIds,
      prompt_version: 'v2',
      model: selectedExerciseGenerationModel,
      review_model: selectedExerciseReviewModel,
      force: exerciseBuild.data?.status === 'succeeded',
    })
    setExerciseCommandId(job.command_id)
  }

  const saveNote = async () => {
    if (!noteContent.trim()) return
    await createNote.mutateAsync({
      chapter_key: chapterKey,
      block_key: noteBlockKey || null,
      content: noteContent.trim(),
    })
    setNoteContent('')
  }

  const chapterNotes = selectChapterNotes(notes.data ?? [], chapterKey)
  const chapterProgress = (progress.data ?? []).find(
    (item) => item.chapter_key === chapterKey && !item.block_key
  )
  const progressStatus = chapterProgress?.status ?? 'not_started'
  const nextProgressStatus = nextChapterProgressStatus(progressStatus)

  if (course.isLoading || outline.isLoading) {
    return <AppShell><CoursePageLoading /></AppShell>
  }
  if (
    (course.isError && isNotFoundError(course.error)) ||
    (outline.isError && isNotFoundError(outline.error))
  ) {
    return <AppShell><div className="flex-1 overflow-y-auto p-6"><CoursePageNotFound /></div></AppShell>
  }
  if (course.isError || outline.isError || !course.data || !outline.data) {
    return <AppShell><div className="flex-1 overflow-y-auto p-6"><CoursePageError onRetry={() => { void course.refetch(); void outline.refetch() }} /></div></AppShell>
  }
  if (!outlineChapter) {
    return <AppShell><div className="flex-1 overflow-y-auto p-6"><CoursePageNotFound /></div></AppShell>
  }
  if (chapter.isError && !isNotFoundError(chapter.error)) {
    return <AppShell><div className="flex-1 overflow-y-auto p-6"><CoursePageError onRetry={() => void chapter.refetch()} /></div></AppShell>
  }
  if (chapter.isLoading) {
    return <AppShell><CoursePageLoading /></AppShell>
  }

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl space-y-6 p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <Button asChild variant="ghost" size="sm" className="-ml-3 mb-2">
                <Link href={`/courses/${encodeURIComponent(courseId)}/outline`}>
                  <ArrowLeft className="mr-2 size-4" />{t('course.backToOutline')}
                </Link>
              </Button>
              <h1 className="font-display text-2xl font-bold">{outlineChapter.title}</h1>
              <p className="mt-1 text-sm text-muted-foreground">{outlineChapter.purpose}</p>
            </div>
            <div className="flex gap-2">
              <Badge variant="secondary">{courseStatusLabel(t, chapter.data?.status ?? 'draft')}</Badge>
              {chapter.data?.version_no && <Badge variant="outline">v{chapter.data.version_no}</Badge>}
            </div>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>{t('course.chapterGeneration')}</CardTitle>
              <CardDescription>{t('course.chapterGenerationDescription')}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="grid gap-5 lg:grid-cols-3">
                {models.isLoading ? (
                  <CourseInlineLoading />
                ) : models.isError ? (
                  <CourseInlineError onRetry={() => void models.refetch()} />
                ) : (
                  <>
                    <div className="space-y-3">
                      <h3 className="font-medium">{t('course.contentModel')}</h3>
                      <CourseModelPicker idPrefix="course-content" accessibleLabel={t('course.contentModel')} options={models.data?.options ?? []} value={contentModel} onChange={setContentModel} disabled={models.isFetching} />
                    </div>
                    <div className="space-y-3">
                      <h3 className="font-medium">{t('course.reviewModel')}</h3>
                      <CourseModelPicker idPrefix="course-review" accessibleLabel={t('course.reviewModel')} options={models.data?.options ?? []} value={reviewModel} onChange={setReviewModel} disabled={models.isFetching} />
                    </div>
                    <div className="space-y-3">
                      <h3 className="font-medium">{t('course.escalationModel')}</h3>
                      <CourseModelPicker idPrefix="course-escalation" accessibleLabel={t('course.escalationModel')} options={models.data?.options ?? []} value={escalationModel} onChange={setEscalationModel} disabled={models.isFetching} />
                    </div>
                  </>
                )}
              </div>

              <div className="space-y-2">
                <p className="text-sm font-medium">{t('course.evidenceAnchors')}</p>
                {anchors.isLoading ? <CourseInlineLoading /> : anchors.isError ? (
                  <CourseInlineError onRetry={() => void anchors.refetch()} />
                ) : anchors.data?.length ? (
                  <div className="grid max-h-[32rem] gap-2 overflow-y-auto rounded-md border p-3 md:grid-cols-2">
                    {(anchors.data ?? []).map((anchor) => (
                      <EvidenceAnchorCard
                        key={anchor.anchor_id}
                        courseId={courseId}
                        anchor={anchor}
                        checked={selectedAnchorIds.includes(anchor.anchor_id)}
                        onCheckedChange={(checked) => setSelectedAnchorIds((previous) => checked
                            ? [...new Set([...previous, anchor.anchor_id])]
                            : previous.filter((item) => item !== anchor.anchor_id)
                          )}
                      />
                    ))}
                  </div>
                ) : <p className="text-sm text-muted-foreground">{t('course.noAnchors')}</p>}
              </div>

              <div className="flex flex-wrap gap-2">
                <Button onClick={() => void generate()} disabled={models.isError || models.isFetching || anchors.isError || !selectedContentModel || !selectedAnchorIds.length || generateChapter.isPending || generationStatus.isFetching}>
                  {requiresNewVersion ? t('course.regenerateChapter') : t('course.generateChapter')}
                </Button>
                <Button variant="outline" onClick={() => void review()} disabled={models.isError || models.isFetching || anchors.isError || !artifact || !selectedReviewModel || !selectedEscalationModel || !selectedAnchorIds.length || !reviewAllowed || reviewChapter.isPending || reviewStatus.isFetching}>
                  {t('course.reviewChapter')}
                </Button>
              </div>
              {artifact && !reviewAllowed && requiresNewVersion && (
                <p className="text-xs text-muted-foreground">{t('course.reviewUnavailable')}</p>
              )}
              <CommandJobPanel status={generationCommandId ? generationStatus.status : undefined} errorMessage={generationStatus.errorMessage} timedOut={generationStatus.isTimedOut} />
              <CommandJobPanel status={reviewCommandId ? reviewStatus.status : undefined} errorMessage={reviewStatus.errorMessage} timedOut={reviewStatus.isTimedOut} />
            </CardContent>
          </Card>

          {!artifact || !currentChapter ? (
            <Alert>
              <FileWarning />
              <AlertTitle>{t('course.chapterNotGenerated')}</AlertTitle>
              <AlertDescription>{t('course.chapterNotGeneratedDescription')}</AlertDescription>
            </Alert>
          ) : (
            <>
              <StructuredDraftEditor courseId={courseId} chapterKey={chapterKey} />

              <ExerciseBankReview
                status={exerciseBuild.data}
                anchors={anchors.data ?? []}
                findings={findings.data ?? []}
                options={models.data?.options ?? []}
                generationModel={exerciseGenerationModel}
                reviewModel={exerciseReviewModel}
                onGenerationModelChange={setExerciseGenerationModel}
                onReviewModelChange={setExerciseReviewModel}
                canGenerate={Boolean(
                  artifact
                  && selectedAnchorIds.length
                  && selectedExerciseGenerationModel
                  && selectedExerciseReviewModel
                  && !models.isError
                  && !anchors.isError
                )}
                isGenerating={generateExerciseBank.isPending || exerciseBuild.isFetching}
                isVerifying={verifyExercise.isPending}
                onGenerate={() => void generateExercises()}
                onVerify={async (exerciseKey, request) => {
                  await verifyExercise.mutateAsync({ exerciseKey, request })
                }}
                onRetry={() => void exerciseBuild.refetch()}
              />

              <Card>
                <CardHeader><CardTitle>{t('course.chapterContent')}</CardTitle></CardHeader>
                <CardContent className="space-y-8">
                  <div>
                    <h3 className="mb-2 font-display font-bold">{t('course.learningObjectives')}</h3>
                    <ul className="list-disc space-y-1 pl-5 text-sm">
                      {artifact.objectives.map((objective) => <li key={objective}>{objective}</li>)}
                    </ul>
                  </div>
                  {artifact.sections.map((section) => (
                    <section key={section.key} id={section.key} className="scroll-mt-6 border-t pt-6">
                      <div className="mb-3 flex items-center justify-between gap-3">
                        <h3 className="font-display text-lg font-bold">{section.title}</h3>
                        <Badge variant="outline">{provenanceLabel(t, section.provenance)}</Badge>
                      </div>
                      <MarkdownRenderer>{section.markdown}</MarkdownRenderer>
                    </section>
                  ))}
                  {artifact.formulas.length > 0 && (
                    <div className="space-y-3">
                      <h3 className="font-display text-lg font-bold">{t('course.formulas')}</h3>
                      {artifact.formulas.map((formula) => (
                        <div key={formula.key} className="rounded-md border p-4">
                          <MarkdownRenderer>{`$$${formula.latex}$$`}</MarkdownRenderer>
                          <p className="text-sm text-muted-foreground">{formula.meaning}</p>
                        </div>
                      ))}
                    </div>
                  )}
                  {artifact.worked_examples.length > 0 && (
                    <div className="space-y-3">
                      <h3 className="font-display text-lg font-bold">{t('course.workedExamples')}</h3>
                      {artifact.worked_examples.map((example) => (
                        <div key={example.key} className="rounded-md border p-4 text-sm">
                          <p className="font-medium">{example.prompt}</p>
                          <ol className="my-3 list-decimal space-y-1 pl-5">
                            {example.steps.map((step) => <li key={step}>{step}</li>)}
                          </ol>
                          <p className="font-medium text-fern">{example.answer}</p>
                        </div>
                      ))}
                    </div>
                  )}
                  {artifact.misconceptions.length > 0 && (
                    <Alert><FileWarning /><AlertTitle>{t('course.misconceptions')}</AlertTitle><AlertDescription>{artifact.misconceptions.join(' · ')}</AlertDescription></Alert>
                  )}
                  {artifact.quick_reference.length > 0 && (
                    <div><h3 className="mb-2 font-display text-lg font-bold">{t('course.quickReference')}</h3><ul className="list-disc space-y-1 pl-5 text-sm">{artifact.quick_reference.map((item) => <li key={item}>{item}</li>)}</ul></div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader><CardTitle className="flex items-center gap-2"><Beaker className="size-5 text-teal" />{t('course.interactiveLabs')}</CardTitle><CardDescription>{t('course.safeLabNotice')}</CardDescription></CardHeader>
                <CardContent className="space-y-6">
                  {labs.isLoading ? <CourseInlineLoading /> : labs.isError ? (
                    <CourseInlineError onRetry={() => void labs.refetch()} />
                  ) : (labs.data ?? []).map((lab) => (
                    <div key={lab.lab_key} className="space-y-3">
                      <LabRenderer spec={lab.spec} />
                      <div className="flex gap-2">
                        <Input
                          aria-label={t('course.attemptAnswer')}
                          value={attemptAnswers[lab.lab_key] ?? ''}
                          onChange={(event) => setAttemptAnswers((current) => ({ ...current, [lab.lab_key]: event.target.value }))}
                          placeholder={t('course.attemptAnswer')}
                        />
                        <Button
                          variant="outline"
                          disabled={!attemptAnswers[lab.lab_key]?.trim() || createAttempt.isPending}
                          onClick={() => createAttempt.mutate({
                            labKey: lab.lab_key,
                            request: { answers: { answer: attemptAnswers[lab.lab_key].trim() } },
                          })}
                        >
                          {t('course.saveAttempt')}
                        </Button>
                      </div>
                    </div>
                  ))}
                  {!labs.isLoading && !labs.isError && !(labs.data?.length) && <p className="text-sm text-muted-foreground">{t('course.noLabs')}</p>}
                </CardContent>
              </Card>

              <Card>
                <CardHeader><CardTitle>{t('course.exercises')}</CardTitle></CardHeader>
                <CardContent>
                  <CourseExercises
                    exercises={artifact.exercises}
                    persistentLabKey={labs.data?.[0]?.lab_key}
                    disabled={labs.isLoading || labs.isError || createAttempt.isPending}
                    onSave={(submission) => createAttempt.mutate(submission)}
                  />
                </CardContent>
              </Card>

              <Card>
                <CardHeader><CardTitle className="flex items-center gap-2"><BookCheck className="size-5 text-fern" />{t('course.reviewAndPublish')}</CardTitle></CardHeader>
                <CardContent>
                  <ChapterPublicationGate
                    chapterStatus={currentChapter.status}
                    findings={findings.data}
                    isLoading={findings.isLoading || findings.isFetching}
                    isError={findings.isError}
                    isUpdating={updateFinding.isPending}
                    isPublishing={publishChapter.isPending}
                    additionalBlockedReason={exerciseBankReady ? null : t('course.exercisePublicationBlocked')}
                    onRetry={() => void findings.refetch()}
                    onUpdate={(findingId, status, resolution_reason) => updateFinding.mutate({ findingId, status, resolution_reason })}
                    onPublish={() => publishChapter.mutate()}
                  />
                </CardContent>
              </Card>

              <div className="grid gap-6 lg:grid-cols-2">
                <Card>
                  <CardHeader><CardTitle className="flex items-center gap-2"><NotebookPen className="size-5 text-gold" />{t('course.notes')}</CardTitle></CardHeader>
                  <CardContent className="space-y-4">
                    {notes.isLoading ? <CourseInlineLoading /> : notes.isError ? (
                      <CourseInlineError onRetry={() => void notes.refetch()} />
                    ) : null}
                    <Textarea value={noteContent} onChange={(event) => setNoteContent(event.target.value)} placeholder={t('course.notePlaceholder')} />
                    <select value={noteBlockKey} onChange={(event) => setNoteBlockKey(event.target.value)} className="flex h-9 w-full rounded-md border border-input bg-background px-3 text-sm">
                      {blockKeys.map((key) => <option key={key} value={key}>{key}</option>)}
                    </select>
                    <Button onClick={() => void saveNote()} disabled={!noteContent.trim() || createNote.isPending}>{t('course.saveNote')}</Button>
                    <div className="space-y-3">
                      {chapterNotes.map((note) => (
                        <div key={note.id} className="rounded-md border p-3 text-sm">
                          <div className="mb-2 flex items-center justify-between gap-2"><Badge variant={note.orphan_status === 'orphaned' ? 'destructive' : 'outline'}>{courseStatusLabel(t, note.orphan_status)}</Badge><span className="font-mono text-xs">{note.block_key}</span></div>
                          <p>{note.content}</p>
                          {note.orphan_status === 'orphaned' && (
                            <div className="mt-3 flex gap-2">
                              <select value={reattachBlocks[note.id] ?? ''} onChange={(event) => setReattachBlocks((current) => ({ ...current, [note.id]: event.target.value }))} className="h-8 flex-1 rounded-md border bg-background px-2 text-xs">
                                <option value="">{t('course.selectBlock')}</option>
                                {blockKeys.map((key) => <option key={key} value={key}>{key}</option>)}
                              </select>
                              <Button size="sm" variant="outline" disabled={!reattachBlocks[note.id] || reattachNote.isPending} onClick={() => reattachNote.mutate({ noteId: note.id, chapter_key: chapterKey, block_key: reattachBlocks[note.id] })}>{t('course.reattach')}</Button>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader><CardTitle>{t('course.learningRecord')}</CardTitle></CardHeader>
                  <CardContent className="space-y-4">
                    {progress.isLoading ? <CourseInlineLoading /> : progress.isError ? (
                      <CourseInlineError onRetry={() => void progress.refetch()} />
                    ) : (
                      <>
                        <div className="flex items-center justify-between gap-3"><span>{t('course.chapterProgress')}</span><Badge variant="outline">{courseStatusLabel(t, progressStatus)}</Badge></div>
                        {nextProgressStatus && (
                          <Button
                            variant={nextProgressStatus === 'completed' ? 'default' : 'outline'}
                            disabled={updateProgress.isPending || !canTransitionChapterProgress(progressStatus, nextProgressStatus)}
                            onClick={() => updateProgress.mutate({ chapter_key: chapterKey, block_key: null, status: nextProgressStatus })}
                          >
                            {nextProgressStatus === 'completed' ? t('course.markComplete') : t('course.markInProgress')}
                          </Button>
                        )}
                      </>
                    )}
                    <div>
                      <h4 className="mb-2 font-medium">{t('course.attemptHistory')}</h4>
                      {attempts.isLoading ? <CourseInlineLoading /> : attempts.isError ? (
                        <CourseInlineError onRetry={() => void attempts.refetch()} />
                      ) : (attempts.data ?? []).map(({ lab_key, attempt }) => (
                        <div key={attempt.id} className="mb-2 rounded-md border p-3 text-xs">
                          <span className="font-mono">{attempt.exercise_key ?? lab_key}</span>
                          <Badge className="ml-2" variant="secondary">{courseStatusLabel(t, attempt.status)}</Badge>
                          <span className="ml-2 text-muted-foreground">{JSON.stringify(attempt.answers)}</span>
                          {attempt.answer_revealed && <Badge className="ml-2" variant="outline">{t('course.answerRevealed')}</Badge>}
                          {attempt.transfer_completed && <Badge className="ml-2" variant="outline">{t('course.transferComplete')}</Badge>}
                        </div>
                      ))}
                      {!attempts.isLoading && !attempts.isError && !(attempts.data?.length) && <p className="text-sm text-muted-foreground">{t('course.noAttempts')}</p>}
                    </div>
                  </CardContent>
                </Card>
              </div>
            </>
          )}
        </div>
      </div>
    </AppShell>
  )
}
