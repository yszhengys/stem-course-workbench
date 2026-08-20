'use client'

import { useEffect } from 'react'

import { AppShell } from '@/components/layout/AppShell'
import { CoursePageError } from '@/components/course/CoursePageState'

export function CourseRouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error('Course route failed', error)
  }, [error])

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto p-6">
        <CoursePageError onRetry={reset} />
      </div>
    </AppShell>
  )
}
