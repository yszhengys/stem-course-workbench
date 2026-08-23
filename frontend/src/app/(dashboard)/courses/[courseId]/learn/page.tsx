'use client'

import { useParams } from 'next/navigation'

import { LearnOverview } from '@/components/course/learning/LearnOverview'
import {
  CoursePageError,
  CoursePageLoading,
  CoursePageNotFound,
} from '@/components/course/CoursePageState'
import { AppShell } from '@/components/layout/AppShell'
import {
  useCourse,
  useCourseLearningOverview,
} from '@/lib/hooks/use-courses'
import { isNotFoundError } from '@/lib/utils/error-handler'

export default function CourseLearnPage() {
  const params = useParams()
  const courseId = params?.courseId ? decodeURIComponent(params.courseId as string) : ''
  const course = useCourse(courseId)
  const overview = useCourseLearningOverview(courseId)

  if (course.isLoading || overview.isLoading) {
    return <AppShell><CoursePageLoading /></AppShell>
  }
  if (
    (course.isError && isNotFoundError(course.error))
    || (overview.isError && isNotFoundError(overview.error))
  ) {
    return (
      <AppShell>
        <div className="flex-1 overflow-y-auto p-6"><CoursePageNotFound /></div>
      </AppShell>
    )
  }
  if (course.isError || overview.isError || !course.data || !overview.data) {
    return (
      <AppShell>
        <div className="flex-1 overflow-y-auto p-6">
          <CoursePageError onRetry={() => {
            void course.refetch()
            void overview.refetch()
          }} />
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <LearnOverview course={course.data} overview={overview.data} />
      </div>
    </AppShell>
  )
}
