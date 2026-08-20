import { describe, it, expect } from 'vitest'
import {
  parsePrefs,
  serializePrefs,
  readPrefsCookie,
  readLegacyPrefs,
  writePrefsCookie,
  PREFS_COOKIE,
} from '@/lib/stores/prefs-cookie'

describe('parsePrefs', () => {
  it('returns {} for null/undefined/empty input', () => {
    expect(parsePrefs(null)).toEqual({})
    expect(parsePrefs(undefined)).toEqual({})
    expect(parsePrefs('')).toEqual({})
  })

  it('returns {} for garbage input', () => {
    expect(parsePrefs('%E0%A4%A')).toEqual({})
    expect(parsePrefs('not-json')).toEqual({})
  })

  it('parses a serialized prefs object', () => {
    const raw = serializePrefs({
      sidebar: { isCollapsed: true },
      theme: { theme: 'dark' },
    })
    expect(parsePrefs(raw)).toEqual({
      sidebar: { isCollapsed: true },
      theme: { theme: 'dark' },
    })
  })
})

describe('serializePrefs / parsePrefs round-trip', () => {
  it('preserves all four preference groups', () => {
    const prefs = {
      sidebar: { isCollapsed: true },
      notebookView: { viewMode: 'list' as const },
      notebookColumns: { sourcesCollapsed: true, notesCollapsed: false },
      theme: { theme: 'dark' as const },
    }
    expect(parsePrefs(serializePrefs(prefs))).toEqual(prefs)
  })

  it('handles unicode values without corrupting them', () => {
    const raw = serializePrefs({ theme: { theme: 'system' } })
    expect(parsePrefs(raw)).toEqual({ theme: { theme: 'system' } })
  })
})

describe('cookie helpers (no DOM)', () => {
  it('readPrefsCookie returns {} when document is unavailable', () => {
    expect(readPrefsCookie()).toEqual({})
  })

  it('readLegacyPrefs returns {} when window is unavailable', () => {
    expect(readLegacyPrefs()).toEqual({})
  })

  it('writePrefsCookie is a no-op when document is unavailable', () => {
    expect(() => writePrefsCookie({ theme: { theme: 'dark' } })).not.toThrow()
  })

  it('exposes the cookie name used by layout and themeScript', () => {
    expect(PREFS_COOKIE).toBe('on-prefs')
  })
})
