'use client'

import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import '@/lib/i18n'
import { LanguageLoadingOverlay } from '@/components/common/LanguageLoadingOverlay'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'

export function I18nProvider({
  children,
  initialPathname,
}: {
  children: React.ReactNode
  initialPathname?: string
}) {
  const { t } = useTranslation()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  // Avoid hydration mismatch while preserving a visible first paint.
  if (!mounted) {
    return (
      <div
        role="status"
        aria-live="polite"
        data-course-workbench-ready={initialPathname === '/courses/new' ? 'new-course' : undefined}
        className="flex min-h-screen items-center justify-center gap-3 bg-background text-muted-foreground"
      >
        <LoadingSpinner />
        <span>{t('common.loading')}</span>
      </div>
    )
  }

  return (
    <>
      <LanguageLoadingOverlay />
      {children}
    </>
  )
}
