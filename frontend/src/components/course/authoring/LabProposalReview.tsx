'use client'

import { useState } from 'react'
import { FlaskConical } from 'lucide-react'

import { LabRenderer } from '@/components/course/LabRenderer'
import { CourseInlineError, CourseInlineLoading } from '@/components/course/CoursePageState'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useApproveCourseLab, useCourseLabs } from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'

interface ApprovalForm {
  confirmation: string
  reason: string
}

const emptyForm: ApprovalForm = { confirmation: '', reason: '' }

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export function LabProposalReview({
  courseId,
  chapterKey,
  canApprove = true,
}: {
  courseId: string
  chapterKey: string
  canApprove?: boolean
}) {
  const { t } = useTranslation()
  const labs = useCourseLabs(courseId, chapterKey)
  const approve = useApproveCourseLab(courseId, chapterKey)
  const [forms, setForms] = useState<Record<string, ApprovalForm>>({})

  const updateForm = (labKey: string, update: Partial<ApprovalForm>) => {
    setForms((current) => ({
      ...current,
      [labKey]: { ...(current[labKey] ?? emptyForm), ...update },
    }))
  }

  if (labs.isLoading) return <CourseInlineLoading />
  if (labs.isError) return <CourseInlineError onRetry={() => void labs.refetch()} />

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FlaskConical className="size-5 text-teal" />
          {t('course.labProposalReviewTitle')}
        </CardTitle>
        <CardDescription>{t('course.labProposalReviewDescription')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {(labs.data ?? []).map((lab) => {
          const form = forms[lab.lab_key] ?? emptyForm
          const legacyBlocked = !lab.proposal_hash || !lab.spec.pedagogy
          const approvalCurrent = Boolean(
            !legacyBlocked
            && lab.approved_hash === lab.proposal_hash
            && lab.approved_at
            && lab.approval_reason,
          )
          const readyToApprove = form.confirmation === '确认实验方案'
            && Boolean(form.reason.trim())
            && !approve.isPending

          return (
            <section
              key={lab.lab_key}
              data-testid={`lab-proposal-${lab.lab_key}`}
              className="space-y-4 rounded-lg border p-4"
            >
              <LabRenderer spec={lab.spec} />
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={approvalCurrent ? 'default' : 'destructive'}>
                  {approvalCurrent
                    ? t('course.labApprovalCurrent')
                    : t('course.labApprovalStale')}
                </Badge>
                <span className="text-xs font-medium text-muted-foreground">
                  {t('course.labProposalHash')}
                </span>
                <code className="break-all text-xs">{lab.proposal_hash ?? '—'}</code>
              </div>

              {legacyBlocked ? (
                <Alert variant="destructive">
                  <AlertTitle>{t('course.labApprovalLegacyBlocked')}</AlertTitle>
                  <AlertDescription>{t('course.labPublicationBlocked')}</AlertDescription>
                </Alert>
              ) : approvalCurrent ? (
                <div className="space-y-1 rounded-md bg-muted p-3 text-sm">
                  <p>{lab.approval_reason}</p>
                  <p className="text-xs text-muted-foreground">
                    {t('course.labApprovedAt')}: {' '}
                    <time dateTime={lab.approved_at ?? undefined}>
                      {lab.approved_at ? new Date(lab.approved_at).toLocaleString() : '—'}
                    </time>
                  </p>
                </div>
              ) : canApprove ? (
                <div className="grid gap-4">
                  <div className="grid gap-2">
                    <Label htmlFor={`lab-confirm-${lab.lab_key}`}>
                      {t('course.labApprovalConfirmation')}
                    </Label>
                    <Input
                      id={`lab-confirm-${lab.lab_key}`}
                      value={form.confirmation}
                      onChange={(event) => updateForm(lab.lab_key, {
                        confirmation: event.target.value,
                      })}
                      placeholder={t('course.labApprovalConfirmationHint', {
                        confirmation: '确认实验方案',
                      })}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor={`lab-reason-${lab.lab_key}`}>
                      {t('course.labApprovalReason')}
                    </Label>
                    <Textarea
                      id={`lab-reason-${lab.lab_key}`}
                      value={form.reason}
                      onChange={(event) => updateForm(lab.lab_key, {
                        reason: event.target.value,
                      })}
                    />
                  </div>
                  <Button
                    type="button"
                    disabled={!readyToApprove}
                    onClick={() => {
                      if (!lab.proposal_hash) return
                      void approve.mutateAsync({
                        labKey: lab.lab_key,
                        request: {
                          confirmation: '确认实验方案',
                          proposal_hash: lab.proposal_hash,
                          reason: form.reason.trim(),
                        },
                      })
                    }}
                  >
                    {t('course.approveLabProposal')}
                  </Button>
                </div>
              ) : null}
            </section>
          )
        })}
        {!labs.data?.length && (
          <p className="text-sm text-muted-foreground">{t('course.noLabs')}</p>
        )}
        {Boolean(approve.error) && (
          <Alert variant="destructive">
            <AlertTitle>{t('course.operationFailed')}</AlertTitle>
            <AlertDescription>
              {errorMessage(approve.error, t('course.operationFailed'))}
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  )
}
