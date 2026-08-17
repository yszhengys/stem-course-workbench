import { create } from 'zustand'

export type Theme = 'light' | 'dark' | 'system'

interface ThemeState {
  theme: Theme
  setTheme: (theme: Theme) => void
  getSystemTheme: () => 'light' | 'dark'
  getEffectiveTheme: () => 'light' | 'dark'
}

// Persistence moved to the on-prefs cookie (see lib/stores/prefs-sync.ts).
// Pre-paint class application stays in the static themeScript in the root
// layout, which now prefers the cookie as well.
export const useThemeStore = create<ThemeState>()((set, get) => ({
  theme: 'system',

  setTheme: (theme: Theme) => {
    set({ theme })

    // Apply theme to document immediately
    if (typeof window !== 'undefined') {
      const root = window.document.documentElement
      const effectiveTheme = theme === 'system' ? get().getSystemTheme() : theme

      root.classList.remove('light', 'dark')
      root.classList.add(effectiveTheme)
      root.setAttribute('data-theme', effectiveTheme)
    }
  },

  getSystemTheme: () => {
    if (typeof window !== 'undefined') {
      return window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
    }
    return 'light'
  },

  getEffectiveTheme: () => {
    const { theme } = get()
    return theme === 'system' ? get().getSystemTheme() : theme
  },
}))

// Hook for components to use theme
export function useTheme() {
  const { theme, setTheme, getEffectiveTheme } = useThemeStore()

  return {
    theme,
    setTheme,
    effectiveTheme: getEffectiveTheme(),
    isDark: getEffectiveTheme() === 'dark',
  }
}
