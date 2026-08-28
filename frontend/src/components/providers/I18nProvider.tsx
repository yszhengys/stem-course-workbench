'use client'

import React, { useEffect, useState } from 'react'
import i18n from '@/lib/i18n'
import { LanguageLoadingOverlay } from '@/components/common/LanguageLoadingOverlay'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'

const HYDRATION_LOADING_TEXT = 'Loading...'

export function I18nProvider({
  children,
  initialPathname,
}: {
  children: React.ReactNode
  initialPathname?: string
}) {
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  useEffect(() => {
    const updateDocumentLanguage = (language: string) => {
      document.documentElement.lang = language || 'en-US'
    }

    updateDocumentLanguage(i18n.resolvedLanguage ?? i18n.language)
    i18n.on('languageChanged', updateDocumentLanguage)
    return () => {
      i18n.off('languageChanged', updateDocumentLanguage)
    }
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
        <span>{HYDRATION_LOADING_TEXT}</span>
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
