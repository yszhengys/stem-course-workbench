'use client'

import Link from 'next/link'
import { BookOpen, Plus } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { CoursePageEmpty, CoursePageError, CoursePageLoading } from '@/components/course/CoursePageState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useCourses } from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'
import { courseStatusLabel, subjectLabel } from '@/lib/course/course-labels'

export default function CoursesPage() {
  const { t } = useTranslation()
  const courses = useCourses()

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="space-y-6 p-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h1 className="font-display text-2xl font-bold tracking-tight">{t('course.listTitle')}</h1>
              <p className="mt-1 text-sm text-muted-foreground">{t('course.listDescription')}</p>
            </div>
            <Button asChild>
              <Link href="/courses/new"><Plus className="mr-2 size-4" />{t('course.newCourse')}</Link>
            </Button>
          </div>

          {courses.isLoading ? (
            <CoursePageLoading />
          ) : courses.isError ? (
            <CoursePageError onRetry={() => void courses.refetch()} />
          ) : courses.data?.length ? (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {courses.data.map((course) => (
                <Link key={course.id} href={`/courses/${encodeURIComponent(course.id)}/outline`}>
                  <Card className="h-full transition-colors hover:border-fern">
                    <CardHeader>
                      <div className="mb-2 flex items-center justify-between gap-3">
                        <BookOpen className="size-5 text-fern" />
                        <Badge variant="secondary">{courseStatusLabel(t, course.status)}</Badge>
                      </div>
                      <CardTitle>{course.title}</CardTitle>
                      <CardDescription>{subjectLabel(t, course.subject)}</CardDescription>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground">
                      {course.description || t('course.noDescription')}
                    </CardContent>
                  </Card>
                </Link>
              ))}
            </div>
          ) : (
            <CoursePageEmpty action={
              <Button asChild><Link href="/courses/new">{t('course.createFirst')}</Link></Button>
            } />
          )}
        </div>
      </div>
    </AppShell>
  )
}
