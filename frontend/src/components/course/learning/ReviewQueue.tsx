'use client'

import Link from 'next/link'
import { CalendarClock } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useTranslation } from '@/lib/hooks/use-translation'
import { masteryStatusLabel } from '@/lib/course/mastery'
import type { CourseLearningOverview, ReviewQueueItem } from '@/lib/types/course'

export function ReviewQueue({
  courseId,
  items,
  concepts,
}: {
  courseId: string
  items: ReviewQueueItem[]
  concepts: CourseLearningOverview['concepts']
}) {
  const { t, language } = useTranslation()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CalendarClock className="size-5" aria-hidden="true" />
          {t('course.reviewQueue')}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('course.reviewQueueEmpty')}</p>
        ) : (
          <ul className="space-y-3">
            {items.map((item) => (
              <li
                key={`${item.chapter_key}:${item.concept_key}`}
                className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3"
              >
                <div>
                  <Link
                    href={`/courses/${encodeURIComponent(courseId)}/learn/${encodeURIComponent(item.chapter_key)}`}
                    className="font-medium underline-offset-4 hover:underline focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {concepts.find((concept) => concept.key === item.concept_key)?.label
                      ?? t('course.conceptLabelUnavailable')}
                  </Link>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t('course.reviewDueDate', {
                      date: new Intl.DateTimeFormat(language, { dateStyle: 'medium' }).format(
                        new Date(item.due_at),
                      ),
                    })}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="secondary">
                    {masteryStatusLabel(t, item.status)}
                  </Badge>
                  <Badge variant="outline">
                    {t('course.reviewInterval', { days: item.interval_days })}
                  </Badge>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
