'use client'

import { AlertCircle, CheckCircle2, Clock3, Loader2 } from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { useTranslation } from '@/lib/hooks/use-translation'
import { courseStatusLabel } from '@/lib/course/course-labels'

interface CommandJobPanelProps {
  status?: string
  errorMessage?: string | null
  timedOut?: boolean
}

export function CommandJobPanel({ status, errorMessage, timedOut = false }: CommandJobPanelProps) {
  const { t } = useTranslation()
  if (!status && !timedOut) return null

  const normalized = status?.toLowerCase()
  const success = normalized === 'completed' || normalized === 'succeeded'
  const failed = ['failed', 'cancelled', 'canceled'].includes(normalized ?? '')
  const running = ['new', 'queued', 'running'].includes(normalized ?? '')
  const Icon = timedOut || failed ? AlertCircle : success ? CheckCircle2 : running ? Loader2 : Clock3

  return (
    <Alert variant={timedOut || failed ? 'destructive' : 'default'}>
      <Icon className={running ? 'animate-spin' : ''} />
      <AlertTitle>{timedOut ? t('course.jobTimedOut') : t('course.jobStatus', { status: courseStatusLabel(t, status ?? '') })}</AlertTitle>
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
