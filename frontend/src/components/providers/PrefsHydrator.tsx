'use client'

import { useEffect, useRef } from 'react'
import { Prefs } from '@/lib/stores/prefs-cookie'
import {
  applyPrefs,
  migrateLegacyPrefsOnce,
  startPrefsPersistence,
} from '@/lib/stores/prefs-sync'

/**
 * Seeds the preference stores from server-read cookies BEFORE any consumer
 * renders — on the server (SSR markup carries the persisted values) and
 * again on the client's first render (same values → no hydration mismatch).
 * After mount it wires cookie write-through and the one-time localStorage
 * migration. Must wrap every component that reads a preference store
 * (i.e. it sits outside ThemeProvider in the root layout).
 */
export function PrefsHydrator({
  initial,
  children,
}: {
  initial: Prefs | null
  children: React.ReactNode
}) {
  const seeded = useRef(false)

  if (!seeded.current) {
    seeded.current = true
    applyPrefs(initial ?? {})
  }

  useEffect(() => {
    startPrefsPersistence()
    migrateLegacyPrefsOnce()
  }, [])

  return <>{children}</>
}
