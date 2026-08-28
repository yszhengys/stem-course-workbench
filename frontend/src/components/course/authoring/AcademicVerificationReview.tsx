'use client'

import { useMemo, useState } from 'react'
import { BookCheck } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  useCourseChapterDraft,
  useVerifyCourseAcademicArtifact,
} from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'
import type {
  AcademicArtifactKind,
  AcademicVerification,
} from '@/lib/types/course'

interface AcademicTarget {
  id: string
  kind: AcademicArtifactKind
  key: string
  label: string
  exactValue: string
  verification: AcademicVerification
}

interface VerificationForm {
  confirmation: string
  reason: string
  anchors: string
}

const emptyForm: VerificationForm = {
  confirmation: '',
  reason: '',
  anchors: '',
}

function parseAnchorIds(value: string): string[] {
  return [...new Set(value.split(',').map((item) => item.trim()).filter(Boolean))]
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export function AcademicVerificationReview({
  courseId,
  chapterKey,
}: {
  courseId: string
  chapterKey: string
}) {
  const { t } = useTranslation()
  const draftQuery = useCourseChapterDraft(courseId, chapterKey)
  const verify = useVerifyCourseAcademicArtifact(courseId, chapterKey)
  const [forms, setForms] = useState<Record<string, VerificationForm>>({})

  const targets = useMemo<AcademicTarget[]>(() => {
    const artifact = draftQuery.data?.artifact
    if (!artifact) return []
    return [
      ...artifact.formulas.map((formula) => ({
        id: `formula-${formula.key}`,
        kind: 'formula' as const,
        key: formula.key,
        label: `${t('course.academicFormula')} · ${formula.key}`,
        exactValue: formula.latex,
        verification: formula.verification,
      })),
      ...artifact.worked_examples.map((example) => ({
        id: `worked_example-${example.key}`,
        kind: 'worked_example' as const,
        key: example.key,
        label: `${t('course.academicWorkedExample')} · ${example.key}`,
        exactValue: example.answer,
        verification: example.verification,
      })),
      ...artifact.exercises.map((exercise) => ({
        id: `legacy_exercise-${exercise.key}`,
        kind: 'legacy_exercise' as const,
        key: exercise.key,
        label: `${t('course.academicLegacyExercise')} · ${exercise.key}`,
        exactValue: exercise.answer,
        verification: exercise.verification,
      })),
    ]
  }, [draftQuery.data?.artifact, t])

  const updateForm = (id: string, update: Partial<VerificationForm>) => {
    setForms((current) => ({
      ...current,
      [id]: { ...(current[id] ?? emptyForm), ...update },
    }))
  }

  const submit = async (target: AcademicTarget) => {
    const draft = draftQuery.data
    const form = forms[target.id] ?? emptyForm
    if (!draft) return
    await verify.mutateAsync({
      targetKind: target.kind,
      targetKey: target.key,
      request: {
        revision_token: draft.revision_token,
        exact_value_confirmation: form.confirmation,
        reason: form.reason,
        anchor_ids: parseAnchorIds(form.anchors),
      },
    })
  }

  if (draftQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
  }
  if (draftQuery.isError || !draftQuery.data) {
    return (
      <Alert variant="destructive">
        <AlertTitle>{t('course.draftLoadFailed')}</AlertTitle>
        <AlertDescription>
          {errorMessage(draftQuery.error, t('course.operationFailed'))}
        </AlertDescription>
      </Alert>
    )
  }

  const draft = draftQuery.data
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookCheck className="size-5" aria-hidden="true" />
          {t('course.academicVerificationTitle')}
        </CardTitle>
        <CardDescription>{t('course.academicVerificationDescription')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {targets.length === 0 && (
          <p className="text-sm text-muted-foreground">{t('course.academicVerificationEmpty')}</p>
        )}
        {targets.map((target) => {
          const form = forms[target.id] ?? emptyForm
          const anchors = parseAnchorIds(form.anchors)
          const canSubmit = (
            draft.editable
            && target.verification.level !== 'L3'
            && form.confirmation === target.exactValue
            && Boolean(form.reason.trim())
            && anchors.length > 0
            && !verify.isPending
          )
          return (
            <section
              key={target.id}
              data-testid={`academic-verification-${target.id}`}
              className="space-y-4 rounded-md border p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="font-medium">{target.label}</h3>
                  <p className="text-xs text-muted-foreground">
                    {t('course.academicVerificationMethod')}: {target.verification.method}
                  </p>
                </div>
                <Badge variant={target.verification.level === 'L0' ? 'destructive' : 'secondary'}>
                  {{
                    L0: t('course.verificationL0'),
                    L1: t('course.verificationL1'),
                    L2: t('course.verificationL2'),
                    L3: t('course.verificationL3'),
                  }[target.verification.level]}
                </Badge>
              </div>
              <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-muted p-3 text-sm">
                {target.exactValue}
              </pre>
              {target.verification.level === 'L1' && (
                <p className="text-xs text-muted-foreground">{t('course.academicL1Notice')}</p>
              )}
              <dl className="grid gap-1 text-xs text-muted-foreground">
                <div><dt className="inline font-medium">{t('course.evidenceAnchors')}:</dt>{' '}<dd className="inline">{target.verification.anchor_ids.join(', ') || '—'}</dd></div>
                {target.verification.reason && <div><dt className="inline font-medium">{t('course.verificationReason')}:</dt>{' '}<dd className="inline">{target.verification.reason}</dd></div>}
                {target.verification.verified_at && <div><dt className="inline font-medium">{t('course.academicVerifiedAt')}:</dt>{' '}<dd className="inline">{target.verification.verified_at}</dd></div>}
              </dl>
              {target.verification.level !== 'L3' && (
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="space-y-2 md:col-span-2">
                    <Label htmlFor={`academic-confirm-${target.id}`}>
                      {t('course.academicExactConfirmation')}
                    </Label>
                    <Input
                      id={`academic-confirm-${target.id}`}
                      aria-label={`${t('course.academicExactConfirmation')} ${target.key}`}
                      value={form.confirmation}
                      onChange={(event) => updateForm(target.id, { confirmation: event.target.value })}
                      disabled={!draft.editable || verify.isPending}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor={`academic-reason-${target.id}`}>
                      {t('course.verificationReason')}
                    </Label>
                    <Textarea
                      id={`academic-reason-${target.id}`}
                      aria-label={`${t('course.verificationReason')} ${target.key}`}
                      value={form.reason}
                      onChange={(event) => updateForm(target.id, { reason: event.target.value })}
                      disabled={!draft.editable || verify.isPending}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor={`academic-anchors-${target.id}`}>
                      {t('course.evidenceAnchors')}
                    </Label>
                    <Input
                      id={`academic-anchors-${target.id}`}
                      aria-label={`${t('course.evidenceAnchors')} ${target.key}`}
                      value={form.anchors}
                      onChange={(event) => updateForm(target.id, { anchors: event.target.value })}
                      disabled={!draft.editable || verify.isPending}
                    />
                  </div>
                  <Button
                    type="button"
                    className="md:col-span-2 md:w-fit"
                    aria-label={`${t('course.academicVerifyHuman')} ${target.key}`}
                    disabled={!canSubmit}
                    onClick={() => void submit(target)}
                  >
                    {t('course.academicVerifyHuman')}
                  </Button>
                </div>
              )}
            </section>
          )
        })}
        {Boolean(verify.error) && (
          <Alert variant="destructive">
            <AlertTitle>{t('course.operationFailed')}</AlertTitle>
            <AlertDescription>
              {errorMessage(verify.error, t('course.operationFailed'))}
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  )
}
