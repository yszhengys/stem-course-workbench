'use client'

import { useEffect, useMemo, useState } from 'react'
import { PencilLine, ShieldCheck } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  useApplyCourseChapterDraftOperation,
  useCourseChapterDraft,
  useValidateCourseChapterDraft,
} from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'
import type {
  CourseDraft,
  CourseDraftValidationResponse,
  DraftOperation,
  ExerciseBlueprint,
  LabSpec,
  TransferTaskSpec,
} from '@/lib/types/course'

type DraftKind = 'text' | 'formula' | 'exercise' | 'transfer' | 'lab'

interface TextBlock {
  key: string
  label: string
  text: string
  anchorIds: string[]
}

function textBlocks(draft: CourseDraft): TextBlock[] {
  const artifact = draft.artifact
  const blocks: TextBlock[] = [{
    key: 'purpose',
    label: 'purpose',
    text: artifact.purpose,
    anchorIds: artifact.attributions.purpose.anchor_ids,
  }]
  artifact.sections.forEach((section) => blocks.push({
    key: section.key,
    label: section.title,
    text: section.markdown,
    anchorIds: section.anchor_ids,
  }))
  const attributedLists = [
    ['prerequisite', artifact.prerequisites, artifact.attributions.prerequisites],
    ['objective', artifact.objectives, artifact.attributions.objectives],
    ['definition', artifact.definitions, artifact.attributions.definitions],
    ['misconception', artifact.misconceptions, artifact.attributions.misconceptions],
    ['pitfall', artifact.pitfalls, artifact.attributions.pitfalls],
    ['quick-reference', artifact.quick_reference, artifact.attributions.quick_reference],
  ] as const
  attributedLists.forEach(([prefix, values, attributions]) => {
    values.forEach((text, index) => blocks.push({
      key: `${prefix}-${index + 1}`,
      label: `${prefix} ${index + 1}`,
      text,
      anchorIds: attributions[index]?.anchor_ids ?? [],
    }))
  })
  artifact.worked_examples.forEach((example) => {
    blocks.push({
      key: `worked-example-${example.key}-prompt`,
      label: `${example.key} prompt`,
      text: example.prompt,
      anchorIds: example.anchor_ids,
    })
    example.steps.forEach((text, index) => blocks.push({
      key: `worked-example-${example.key}-step-${index + 1}`,
      label: `${example.key} step ${index + 1}`,
      text,
      anchorIds: example.anchor_ids,
    }))
    blocks.push({
      key: `worked-example-${example.key}-answer`,
      label: `${example.key} answer`,
      text: example.answer,
      anchorIds: example.anchor_ids,
    })
  })
  artifact.exercises.forEach((exercise) => {
    const prefix = `legacy-exercise-${exercise.key}`
    blocks.push({
      key: `${prefix}-prompt`, label: `${exercise.key} prompt`,
      text: exercise.prompt, anchorIds: exercise.anchor_ids,
    })
    exercise.hints.forEach((text, index) => blocks.push({
      key: `${prefix}-hint-${index + 1}`, label: `${exercise.key} hint ${index + 1}`,
      text, anchorIds: exercise.anchor_ids,
    }))
    blocks.push({
      key: `${prefix}-answer`, label: `${exercise.key} answer`,
      text: exercise.answer, anchorIds: exercise.anchor_ids,
    })
    blocks.push({
      key: `${prefix}-transfer`, label: `${exercise.key} transfer`,
      text: exercise.transfer_task, anchorIds: exercise.anchor_ids,
    })
  })
  return blocks
}

function commaSeparated(values: string[]): string {
  return values.join(', ')
}

function parseAnchorIds(value: string): string[] {
  return [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))]
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export function StructuredDraftEditor({
  courseId,
  chapterKey,
}: {
  courseId: string
  chapterKey: string
}) {
  const { t } = useTranslation()
  const query = useCourseChapterDraft(courseId, chapterKey)
  const apply = useApplyCourseChapterDraftOperation(courseId, chapterKey)
  const validate = useValidateCourseChapterDraft(courseId, chapterKey)
  const [kind, setKind] = useState<DraftKind>('text')
  const [blockKey, setBlockKey] = useState('')
  const [value, setValue] = useState('')
  const [hints, setHints] = useState('')
  const [anchorIds, setAnchorIds] = useState('')
  const [validation, setValidation] = useState<CourseDraftValidationResponse | null>(null)

  const draft = query.data
  const blocks = useMemo(() => draft ? textBlocks(draft) : [], [draft])
  const transfers = useMemo(
    () => (draft?.exercises ?? []).filter(
      (exercise): exercise is ExerciseBlueprint & { transfer_task: TransferTaskSpec } => (
        exercise.transfer_task !== null
      ),
    ),
    [draft?.exercises],
  )

  const initializeSelection = (nextKind: DraftKind, preferredKey?: string) => {
    if (!draft) return
    if (nextKind === 'text') {
      const item = blocks.find((candidate) => candidate.key === preferredKey) ?? blocks[0]
      setBlockKey(item?.key ?? '')
      setValue(item?.text ?? '')
      setHints('')
      setAnchorIds(commaSeparated(item?.anchorIds ?? []))
    } else if (nextKind === 'formula') {
      const item = draft.artifact.formulas.find((candidate) => candidate.key === preferredKey)
        ?? draft.artifact.formulas[0]
      setBlockKey(item?.key ?? '')
      setValue(item?.latex ?? '')
      setHints('')
      setAnchorIds(commaSeparated(item?.anchor_ids ?? []))
    } else if (nextKind === 'exercise') {
      const item = draft.exercises.find((candidate) => candidate.key === preferredKey)
        ?? draft.exercises[0]
      setBlockKey(item?.key ?? '')
      setValue(item?.prompt ?? '')
      setHints((item?.hints ?? []).join('\n'))
      setAnchorIds('')
    } else if (nextKind === 'transfer') {
      const item = transfers.find((candidate) => candidate.transfer_task.key === preferredKey)
        ?? transfers[0]
      setBlockKey(item?.transfer_task.key ?? '')
      setValue(item?.transfer_task.prompt ?? '')
      setHints('')
      setAnchorIds('')
    } else {
      const item = draft.artifact.labs.find((candidate) => candidate.key === preferredKey)
        ?? draft.artifact.labs[0]
      setBlockKey(item?.key ?? '')
      setValue(item?.title ?? '')
      setHints('')
      setAnchorIds('')
    }
  }

  useEffect(() => {
    if (!draft || blockKey) return
    initializeSelection(kind)
    // This initialization should run once for each newly loaded draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, blockKey, kind])

  const selectKind = (nextKind: DraftKind) => {
    setKind(nextKind)
    initializeSelection(nextKind)
  }

  const selectBlock = (nextKey: string) => {
    initializeSelection(kind, nextKey)
  }

  const operation = (): DraftOperation | null => {
    if (!draft || !blockKey || !value.trim()) return null
    if (kind === 'text') {
      return {
        kind: 'replace_text', block_key: blockKey,
        text: value.trim(), anchor_ids: parseAnchorIds(anchorIds),
      }
    }
    if (kind === 'formula') {
      return {
        kind: 'replace_formula', block_key: blockKey,
        latex: value.trim(), anchor_ids: parseAnchorIds(anchorIds),
      }
    }
    if (kind === 'exercise') {
      const exercise = draft.exercises.find((item) => item.key === blockKey)
      if (!exercise) return null
      return {
        kind: 'replace_exercise', block_key: blockKey,
        exercise: {
          ...exercise,
          prompt: value.trim(),
          hints: hints.split('\n').map((hint) => hint.trim()).filter(Boolean),
        },
      }
    }
    if (kind === 'transfer') {
      const transfer = transfers.find((item) => item.transfer_task.key === blockKey)?.transfer_task
      if (!transfer) return null
      return {
        kind: 'replace_transfer', block_key: blockKey,
        transfer_task: { ...transfer, prompt: value.trim() },
      }
    }
    const lab = draft.artifact.labs.find((item) => item.key === blockKey)
    if (!lab) return null
    return {
      kind: 'replace_lab', block_key: blockKey,
      lab_spec: { ...lab, title: value.trim() } as LabSpec,
    }
  }

  const save = async () => {
    if (!draft) return
    const nextOperation = operation()
    if (!nextOperation) return
    await apply.mutateAsync({
      revision_token: draft.revision_token,
      operation: nextOperation,
    })
    setValidation(null)
  }

  const runValidation = async () => {
    if (!draft) return
    const result = await validate.mutateAsync({ revision_token: draft.revision_token })
    setValidation(result)
  }

  if (query.isLoading) {
    return <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
  }
  if (query.isError || !draft) {
    return (
      <Alert variant="destructive">
        <AlertTitle>{t('course.draftLoadFailed')}</AlertTitle>
        <AlertDescription className="space-y-2">
          <p>{errorMessage(query.error, t('course.operationFailed'))}</p>
          <Button type="button" variant="outline" onClick={() => void query.refetch()}>
            {t('common.retry')}
          </Button>
        </AlertDescription>
      </Alert>
    )
  }

  const hasSelection = Boolean(blockKey && value.trim())
  const saveDisabled = !draft.editable || !hasSelection || apply.isPending
  const validateDisabled = (
    !draft.editable
    || draft.revision_no === 0
    || draft.revision_status === 'validated'
    || validate.isPending
  )

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <PencilLine className="size-5" aria-hidden="true" />
              {t('course.structuredDraftEditor')}
            </CardTitle>
            <CardDescription>{t('course.structuredDraftEditorDescription')}</CardDescription>
          </div>
          <div className="flex gap-2">
            <Badge variant="outline">{t('course.draftRevision')} {draft.revision_no}</Badge>
            <Badge variant={draft.revision_status === 'validated' ? 'default' : 'secondary'}>
              {draft.revision_status ?? t('course.draftUnedited')}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {!draft.editable && (
          <Alert>
            <AlertTitle>{t('course.draftReadOnly')}</AlertTitle>
            <AlertDescription>{t('course.draftReadOnlyDescription')}</AlertDescription>
          </Alert>
        )}

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="course-draft-kind">{t('course.draftKind')}</Label>
            <select
              id="course-draft-kind"
              aria-label={t('course.draftKind')}
              value={kind}
              onChange={(event) => selectKind(event.target.value as DraftKind)}
              disabled={!draft.editable || apply.isPending}
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
            >
              <option value="text">{t('course.draftKindText')}</option>
              <option value="formula" disabled={draft.artifact.formulas.length === 0}>{t('course.draftKindFormula')}</option>
              <option value="exercise" disabled={draft.exercises.length === 0}>{t('course.draftKindExercise')}</option>
              <option value="transfer" disabled={transfers.length === 0}>{t('course.draftKindTransfer')}</option>
              <option value="lab" disabled={draft.artifact.labs.length === 0}>{t('course.draftKindLab')}</option>
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="course-draft-block">{t('course.draftBlock')}</Label>
            <select
              id="course-draft-block"
              aria-label={t('course.draftBlock')}
              value={blockKey}
              onChange={(event) => selectBlock(event.target.value)}
              disabled={!draft.editable || apply.isPending}
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
            >
              {kind === 'text' && blocks.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
              {kind === 'formula' && draft.artifact.formulas.map((item) => <option key={item.key} value={item.key}>{item.key}</option>)}
              {kind === 'exercise' && draft.exercises.map((item) => <option key={item.key} value={item.key}>{item.key}</option>)}
              {kind === 'transfer' && transfers.map((item) => <option key={item.transfer_task.key} value={item.transfer_task.key}>{item.transfer_task.key}</option>)}
              {kind === 'lab' && draft.artifact.labs.map((item) => <option key={item.key} value={item.key}>{item.key}</option>)}
            </select>
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="course-draft-value">
            {kind === 'text' && t('course.draftText')}
            {kind === 'formula' && t('course.draftFormula')}
            {kind === 'exercise' && t('course.draftExercisePrompt')}
            {kind === 'transfer' && t('course.draftTransferPrompt')}
            {kind === 'lab' && t('course.draftLabTitle')}
          </Label>
          <Textarea
            id="course-draft-value"
            aria-label={
              kind === 'text' ? t('course.draftText')
                : kind === 'formula' ? t('course.draftFormula')
                  : kind === 'exercise' ? t('course.draftExercisePrompt')
                    : kind === 'transfer' ? t('course.draftTransferPrompt')
                      : t('course.draftLabTitle')
            }
            value={value}
            onChange={(event) => setValue(event.target.value)}
            disabled={!draft.editable || apply.isPending}
            rows={kind === 'formula' || kind === 'lab' ? 2 : 6}
          />
        </div>

        {kind === 'exercise' && (
          <div className="space-y-2">
            <Label htmlFor="course-draft-hints">{t('course.draftExerciseHints')}</Label>
            <Textarea
              id="course-draft-hints"
              aria-label={t('course.draftExerciseHints')}
              value={hints}
              onChange={(event) => setHints(event.target.value)}
              disabled={!draft.editable || apply.isPending}
              rows={4}
            />
            <p className="text-xs text-muted-foreground">{t('course.draftExerciseHintsDescription')}</p>
          </div>
        )}

        {(kind === 'text' || kind === 'formula') && (
          <div className="space-y-2">
            <Label htmlFor="course-draft-anchors">{t('course.draftAnchorIds')}</Label>
            <Input
              id="course-draft-anchors"
              value={anchorIds}
              onChange={(event) => setAnchorIds(event.target.value)}
              disabled={!draft.editable || apply.isPending}
            />
            <p className="text-xs text-muted-foreground">{t('course.draftAnchorIdsDescription')}</p>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <Button type="button" onClick={() => void save()} disabled={saveDisabled}>
            {t('course.saveDraftChange')}
          </Button>
          <Button type="button" variant="outline" onClick={() => void runValidation()} disabled={validateDisabled}>
            <ShieldCheck className="mr-2 size-4" aria-hidden="true" />
            {t('course.validateDraft')}
          </Button>
        </div>

        {Boolean(apply.error || validate.error) && (
          <Alert variant="destructive">
            <AlertTitle>{t('course.draftOperationFailed')}</AlertTitle>
            <AlertDescription>
              {errorMessage(apply.error ?? validate.error, t('course.operationFailed'))}
            </AlertDescription>
          </Alert>
        )}

        {validation && (
          <Alert variant={validation.valid ? 'default' : 'destructive'}>
            <AlertTitle>
              {validation.valid ? t('course.draftValidationPassed') : t('course.draftValidationBlocked')}
            </AlertTitle>
            <AlertDescription className="space-y-2">
              <p>{validation.checked.join(' · ')}</p>
              {validation.findings.map((finding, index) => (
                <p key={`${finding.kind}-${finding.item_key}-${index}`}>{finding.message}</p>
              ))}
            </AlertDescription>
          </Alert>
        )}

        <p className="text-xs text-muted-foreground">{t('course.draftSafetyNotice')}</p>
      </CardContent>
    </Card>
  )
}
