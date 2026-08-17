'use client'

import { useSidebarStore } from '@/lib/stores/sidebar-store'
import { useNotebookViewStore } from '@/lib/stores/notebook-view-store'
import { useNotebookColumnsStore } from '@/lib/stores/notebook-columns-store'
import { useThemeStore } from '@/lib/stores/theme-store'
import {
  clearLegacyPrefs,
  Prefs,
  readLegacyPrefs,
  writePrefsCookie,
} from '@/lib/stores/prefs-cookie'

export type { Prefs }

/** Seed the stores with server-provided cookie values (SSR + hydration). */
export function applyPrefs(prefs: Prefs): void {
  if (prefs.sidebar) {
    useSidebarStore.setState({ isCollapsed: prefs.sidebar.isCollapsed })
  }
  if (prefs.notebookView) {
    useNotebookViewStore.setState({ viewMode: prefs.notebookView.viewMode })
  }
  if (prefs.notebookColumns) {
    useNotebookColumnsStore.setState({
      sourcesCollapsed: prefs.notebookColumns.sourcesCollapsed,
      notesCollapsed: prefs.notebookColumns.notesCollapsed,
    })
  }
  if (prefs.theme) {
    useThemeStore.setState({ theme: prefs.theme.theme })
  }
}

export function collectPrefs(): Prefs {
  return {
    sidebar: { isCollapsed: useSidebarStore.getState().isCollapsed },
    notebookView: { viewMode: useNotebookViewStore.getState().viewMode },
    notebookColumns: {
      sourcesCollapsed: useNotebookColumnsStore.getState().sourcesCollapsed,
      notesCollapsed: useNotebookColumnsStore.getState().notesCollapsed,
    },
    theme: { theme: useThemeStore.getState().theme },
  }
}

let persistenceStarted = false

/** Write-through: every preference change is persisted to the cookie. */
export function startPrefsPersistence(): void {
  if (persistenceStarted || typeof window === 'undefined') return
  persistenceStarted = true

  const persistNow = () => writePrefsCookie(collectPrefs())
  useSidebarStore.subscribe(persistNow)
  useNotebookViewStore.subscribe(persistNow)
  useNotebookColumnsStore.subscribe(persistNow)
  useThemeStore.subscribe(persistNow)
}

let migrationAttempted = false

/**
 * One-time migration from the legacy localStorage keys to the cookie.
 * Runs after hydration: the first server render cannot know legacy
 * localStorage, so it renders defaults; this upgrades the stores and writes
 * the cookie, and every subsequent load is fully consistent.
 */
export function migrateLegacyPrefsOnce(): void {
  if (migrationAttempted || typeof window === 'undefined') return
  migrationAttempted = true
  if (document.cookie.includes('on-prefs=')) return

  const legacy = readLegacyPrefs()
  if (Object.keys(legacy).length === 0) return
  applyPrefs(legacy)
  writePrefsCookie(collectPrefs())
  clearLegacyPrefs()
}
