/**
 * Cookie-backed UI preferences — the single source of truth for the four
 * preference stores (sidebar, notebook-view, notebook-columns, theme).
 *
 * Why a cookie instead of localStorage: localStorage is invisible to the
 * server, so SSR rendered the store defaults while the client's first render
 * (zustand v5 hydrates synchronously from localStorage) used the persisted
 * values — a hydration mismatch. A cookie lets the server render the SAME
 * values, so both the server HTML and the first client render agree: no
 * mismatch, no flash of default state.
 */

export const PREFS_COOKIE = 'on-prefs'

export type ViewMode = 'tile' | 'list'
export type ThemePref = 'light' | 'dark' | 'system'

export interface SidebarPrefs {
  isCollapsed: boolean
}

export interface NotebookViewPrefs {
  viewMode: ViewMode
}

export interface NotebookColumnsPrefs {
  sourcesCollapsed: boolean
  notesCollapsed: boolean
}

export interface ThemePrefs {
  theme: ThemePref
}

export interface Prefs {
  sidebar?: SidebarPrefs
  notebookView?: NotebookViewPrefs
  notebookColumns?: NotebookColumnsPrefs
  theme?: ThemePrefs
}

export function parsePrefs(raw: string | undefined | null): Prefs {
  if (!raw) return {}
  try {
    const parsed = JSON.parse(decodeURIComponent(raw))
    return parsed && typeof parsed === 'object' ? (parsed as Prefs) : {}
  } catch {
    return {}
  }
}

export function serializePrefs(prefs: Prefs): string {
  return encodeURIComponent(JSON.stringify(prefs))
}

export function readPrefsCookie(): Prefs {
  if (typeof document === 'undefined') return {}
  const match = document.cookie.match(
    new RegExp('(?:^|; )' + PREFS_COOKIE + '=([^;]*)')
  )
  return parsePrefs(match?.[1])
}

export function writePrefsCookie(prefs: Prefs): void {
  if (typeof document === 'undefined') return
  document.cookie = `${PREFS_COOKIE}=${serializePrefs(prefs)}; path=/; max-age=${
    60 * 60 * 24 * 365
  }; SameSite=Lax`
}

// Legacy localStorage keys written by the removed zustand `persist`
// middleware. Read once for migration, then cleared.
const LEGACY_STORAGE_KEYS = [
  'sidebar-storage',
  'notebook-view-storage',
  'notebook-columns-storage',
  'theme-storage',
] as const

export function readLegacyPrefs(): Prefs {
  if (typeof window === 'undefined') return {}
  const prefs: Prefs = {}
  try {
    const read = (key: string): unknown => {
      const raw = window.localStorage.getItem(key)
      return raw ? JSON.parse(raw) : null
    }

    const sidebar = read('sidebar-storage') as {
      state?: { isCollapsed?: boolean }
    } | null
    if (typeof sidebar?.state?.isCollapsed === 'boolean') {
      prefs.sidebar = { isCollapsed: sidebar.state.isCollapsed }
    }

    const view = read('notebook-view-storage') as {
      state?: { viewMode?: ViewMode }
    } | null
    if (view?.state?.viewMode === 'tile' || view?.state?.viewMode === 'list') {
      prefs.notebookView = { viewMode: view.state.viewMode }
    }

    const cols = read('notebook-columns-storage') as {
      state?: { sourcesCollapsed?: boolean; notesCollapsed?: boolean }
    } | null
    if (cols?.state) {
      prefs.notebookColumns = {
        sourcesCollapsed: !!cols.state.sourcesCollapsed,
        notesCollapsed: !!cols.state.notesCollapsed,
      }
    }

    const theme = read('theme-storage') as {
      state?: { theme?: ThemePref }
    } | null
    if (
      theme?.state?.theme === 'light' ||
      theme?.state?.theme === 'dark' ||
      theme?.state?.theme === 'system'
    ) {
      prefs.theme = { theme: theme.state.theme }
    }
  } catch {
    // Corrupt legacy entries are ignored; the cookie wins next write.
  }
  return prefs
}

export function clearLegacyPrefs(): void {
  if (typeof window === 'undefined') return
  for (const key of LEGACY_STORAGE_KEYS) {
    try {
      window.localStorage.removeItem(key)
    } catch {
      // ignore
    }
  }
}
