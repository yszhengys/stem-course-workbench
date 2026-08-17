'use client'

import { useAuth } from '@/lib/hooks/use-auth'
import { useVersionCheck } from '@/lib/hooks/use-version-check'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { ErrorBoundary } from '@/components/common/ErrorBoundary'
import { ModalProvider } from '@/components/providers/ModalProvider'
import { CreateDialogsProvider } from '@/lib/hooks/use-create-dialogs'
import { CommandPalette } from '@/components/common/CommandPalette'
import { useTranslation } from '@/lib/hooks/use-translation'

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const { t } = useTranslation()
  const { isAuthenticated, isLoading } = useAuth()
  const router = useRouter()
  const pathname = usePathname()
  const [hasCheckedAuth, setHasCheckedAuth] = useState(false)

  // Check for version updates once per session
  useVersionCheck()

  useEffect(() => {
    // Mark that we've completed the initial auth check
    if (!isLoading) {
      setHasCheckedAuth(true)

      // Redirect to login if not authenticated
      if (!isAuthenticated) {
        // Store the current path to redirect back after login
        const currentPath = window.location.pathname + window.location.search
        sessionStorage.setItem('redirectAfterLogin', currentPath)
        router.push('/login')
      }
    }
  }, [isAuthenticated, isLoading, router])

  // Show loading spinner during initial auth check or while loading
  if (isLoading || !hasCheckedAuth) {
    return (
      <div
        role="status"
        aria-live="polite"
        data-course-workbench-ready={pathname === '/courses/new' ? 'new-course' : undefined}
        className="min-h-screen flex items-center justify-center gap-3"
      >
        <LoadingSpinner />
        <span className="text-sm text-muted-foreground">{t('common.loading')}</span>
      </div>
    )
  }

  // Keep a visible redirect state rather than returning a blank document.
  if (!isAuthenticated) {
    return (
      <div
        role="status"
        aria-live="polite"
        data-course-workbench-ready={pathname === '/courses/new' ? 'new-course' : undefined}
        className="min-h-screen flex items-center justify-center gap-3"
      >
        <LoadingSpinner />
        <span className="text-sm text-muted-foreground">{t('common.loading')}</span>
      </div>
    )
  }

  return (
    <ErrorBoundary>
      <CreateDialogsProvider>
        {children}
        <ModalProvider />
        <CommandPalette />
      </CreateDialogsProvider>
    </ErrorBoundary>
  )
}
