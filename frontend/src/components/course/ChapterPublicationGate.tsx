'use client'

import { Button } from '@/components/ui/button'
import { CourseInlineError, CourseInlineLoading } from '@/components/course/CoursePageState'
import { ValidationFindingsPanel } from '@/components/course/ValidationFindingsPanel'
import { isFindingBlockingPublication } from '@/lib/course/publication-policy'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { CourseFinding } from '@/lib/types/course'

export function ChapterPublicationGate({
  chapterStatus,
  findings,
  isLoading,
  isError,
  isUpdating,
  isPublishing,
  additionalBlockedReason,
  onRetry,
  onUpdate,
  onPublish,
}: {
  chapterStatus: string
  findings: CourseFinding[] | undefined
  isLoading: boolean
  isError: boolean
  isUpdating: boolean
  isPublishing: boolean
  additionalBlockedReason?: string | null
  onRetry: () => void
  onUpdate: (findingId: string, status: 'resolved' | 'acknowledged', reason: string) => void
  onPublish: () => void
}) {
  const { t } = useTranslation()
  const findingsKnown = !isLoading && !isError && findings !== undefined
  const findingsBlockPublication = findings?.some((record) =>
    isFindingBlockingPublication(record.finding)
  ) ?? false
  const publicationBlocked = !findingsKnown
    || findingsBlockPublication
    || Boolean(additionalBlockedReason)

  return (
    <div className="space-y-5">
      {isLoading ? (
        <CourseInlineLoading label={t('course.validationLoading')} />
      ) : isError || findings === undefined ? (
        <CourseInlineError onRetry={onRetry} />
      ) : (
        <ValidationFindingsPanel
          findings={findings}
          disabled={isUpdating}
          onUpdate={onUpdate}
        />
      )}
      {findingsKnown && findingsBlockPublication && (
        <p className="text-sm text-destructive">{t('course.publishBlocked')}</p>
      )}
      {findingsKnown && additionalBlockedReason && (
        <p className="text-sm text-destructive">{additionalBlockedReason}</p>
      )}
      <Button
        onClick={onPublish}
        disabled={chapterStatus !== 'ready' || publicationBlocked || isPublishing}
      >
        {chapterStatus === 'published' ? t('course.published') : t('course.publishChapter')}
      </Button>
    </div>
  )
}
