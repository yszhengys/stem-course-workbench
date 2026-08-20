'use client'

import { AlertCircle, CheckCircle2, Clock3, Loader2 } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { useTranslation } from '@/lib/hooks/use-translation'
import { courseStatusLabel } from '@/lib/course/course-labels'
import {
  commandJobStateSchema,
  type CommandJobState,
} from '@/lib/api/commands'

interface CommandJobPanelProps {
  status?: CommandJobState
  errorMessage?: string | null
  timedOut?: boolean
}

export function CommandJobPanel({ status, errorMessage, timedOut = false }: CommandJobPanelProps) {
  const { t } = useTranslation()
  if (!status && !timedOut) return null

  const statusResult = status === undefined ? null : commandJobStateSchema.safeParse(status)
  const invalidStatus = Boolean(statusResult && !statusResult.success)
  const normalized = statusResult?.success ? statusResult.data : undefined
  const success = normalized === 'completed' || normalized === 'succeeded'
  const failed = invalidStatus || ['failed', 'cancelled', 'canceled'].includes(normalized ?? '')
  const running = !invalidStatus && ['new', 'queued', 'running'].includes(normalized ?? '')
  const Icon = timedOut || failed ? AlertCircle : success ? CheckCircle2 : running ? Loader2 : Clock3

  return (
    <Alert variant={timedOut || failed ? 'destructive' : 'default'}>
      <Icon className={running ? 'animate-spin' : ''} />
      <AlertTitle>{timedOut ? t('course.jobTimedOut') : t('course.jobStatus', { status: courseStatusLabel(t, invalidStatus ? 'failed' : (normalized ?? '')) })}</AlertTitle>
      <AlertDescription>
        {timedOut
          ? t('course.jobTimedOutDescription')
          : failed
            ? (errorMessage || t('course.operationFailed'))
            : running
              ? t('course.jobRunningDescription')
              : t('course.jobFinishedDescription')}
      </AlertDescription>
    </Alert>
  )
}
