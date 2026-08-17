'use client'

import { AlertCircle, FileQuestion, GraduationCap } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/common/EmptyState'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { useTranslation } from '@/lib/hooks/use-translation'

export function CoursePageLoading() {
  const { t } = useTranslation()
  return (
    <div role="status" className="flex min-h-[50vh] items-center justify-center gap-3 text-muted-foreground">
      <LoadingSpinner />
      <span>{t('common.loading')}</span>
    </div>
  )
}

export function CoursePageError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <EmptyState
      icon={AlertCircle}
      title={t('course.loadFailed')}
      description={t('course.loadFailedDescription')}
      action={<Button onClick={onRetry}>{t('common.retry')}</Button>}
    />
  )
}

export function CoursePageEmpty({ action }: { action?: React.ReactNode }) {
  const { t } = useTranslation()
  return (
    <EmptyState
      icon={GraduationCap}
      title={t('course.emptyTitle')}
      description={t('course.emptyDescription')}
      action={action}
    />
  )
}

export function CoursePageNotFound() {
  const { t } = useTranslation()
  return (
    <EmptyState
      icon={FileQuestion}
      title={t('course.notFoundTitle')}
      description={t('course.notFoundDescription')}
    />
  )
}
