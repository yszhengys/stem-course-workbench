'use client'

import Link from 'next/link'
import { ArrowRight, BookOpen, Hammer } from 'lucide-react'

import { ReviewQueue } from '@/components/course/learning/ReviewQueue'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useTranslation } from '@/lib/hooks/use-translation'
import {
  conceptLabel,
  masteryStatusLabel,
  selectResumeChapter,
} from '@/lib/course/mastery'
import type { Course, CourseLearningOverview } from '@/lib/types/course'

export function LearnOverview({
  course,
  overview,
}: {
  course: Course
  overview: CourseLearningOverview
}) {
  const { t } = useTranslation()
  const resume = selectResumeChapter(overview)

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-primary">{t('course.learnMode')}</p>
          <h1 className="font-display text-3xl font-bold">{course.title}</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            {t('course.learnOverviewDescription')}
          </p>
        </div>
        <Button asChild variant="outline">
          <Link href={`/courses/${encodeURIComponent(course.id)}/outline`}>
            <Hammer aria-hidden="true" />
            {t('course.openBuildMode')}
          </Link>
        </Button>
      </header>

      {resume && (
        <Card className="border-primary/30 bg-primary/5">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 pt-0">
            <div>
              <p className="text-sm text-muted-foreground">{t('course.resumeFrom')}</p>
              <p className="font-display text-xl font-bold">{resume.title}</p>
            </div>
            <Button asChild>
              <Link href={`/courses/${encodeURIComponent(course.id)}/learn/${encodeURIComponent(resume.chapter_key)}`}>
                {t('course.continueLearning')}
                <ArrowRight aria-hidden="true" />
              </Link>
            </Button>
          </CardContent>
        </Card>
      )}

      <section aria-labelledby="learn-chapters-title">
        <div className="mb-3 flex items-center gap-2">
          <BookOpen className="size-5" aria-hidden="true" />
          <h2 id="learn-chapters-title" className="font-display text-xl font-bold">
            {t('course.learnChapters')}
          </h2>
        </div>
        {overview.chapters.length === 0 ? (
          <p className="rounded-md border p-4 text-sm text-muted-foreground">
            {t('course.noPublishedChapters')}
          </p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {overview.chapters.map((chapter) => {
              const masteries = overview.masteries.filter(
                (mastery) => mastery.chapter_key === chapter.chapter_key,
              )
              return (
                <Card key={chapter.chapter_key}>
                  <CardHeader>
                    <CardTitle>
                      <Link
                        href={`/courses/${encodeURIComponent(course.id)}/learn/${encodeURIComponent(chapter.chapter_key)}`}
                        className="underline-offset-4 hover:underline focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {chapter.title}
                      </Link>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <p className="text-sm text-muted-foreground">
                      {chapter.latest_position
                        ? t('course.chapterHasPosition')
                        : t('course.chapterNotOpened')}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {masteries.length === 0 ? (
                        <Badge variant="outline">
                          {masteryStatusLabel(t, 'not_started')}
                        </Badge>
                      ) : masteries.map((mastery) => (
                        <Badge key={mastery.concept_key} variant="secondary">
                          {conceptLabel(overview, mastery.concept_key)
                            ?? t('course.conceptLabelUnavailable')}: {masteryStatusLabel(t, mastery.status)}
                        </Badge>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              )
            })}
          </div>
        )}
      </section>

      <section aria-label={t('course.reviewQueue')}>
        <ReviewQueue
          courseId={course.id}
          items={overview.review_queue}
          concepts={overview.concepts}
        />
      </section>
    </div>
  )
}
