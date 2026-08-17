import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface NavigationState {
  returnTo?: {
    path: string
    label: string
    preserveState?: {
      scrollPosition?: number
      highlightItemId?: string
      timestamp?: number
    }
  }
  hasHydrated: boolean
  setHasHydrated: (hydrated: boolean) => void
  setReturnTo: (path: string, label: string, preserveState?: object) => void
  clearReturnTo: () => void
  getReturnPath: () => string
  getReturnLabel: () => string
}

// sessionStorage cannot be mirrored to the server (it is per-tab), so unlike
// the cookie-backed preference stores this one hydrates client-side with
// skipHydration + hasHydrated: SSR and the first client render both use the
// defaults (no mismatch), then the stored per-tab value applies. Consumers
// render the fallback ("Back to Sources") until hasHydrated is true.
export const useNavigationStore = create<NavigationState>()(
  persist(
    (set, get) => ({
      returnTo: undefined,
      hasHydrated: false,

      setHasHydrated: (hydrated: boolean) => set({ hasHydrated: hydrated }),

      setReturnTo: (path, label, preserveState) => set({
        returnTo: {
          path,
          label,
          preserveState: {
            ...preserveState,
            timestamp: Date.now()
          }
        }
      }),

      clearReturnTo: () => set({ returnTo: undefined }),

      // Pure getters: staleness is handled by falling back without mutating
      // the store — a set() here used to run during render (React warning /
      // re-render loop risk). The stale entry is simply overwritten on the
      // next setReturnTo.
      getReturnPath: () => {
        const returnTo = get().returnTo

        // Check if context is stale (older than 1 hour)
        if (returnTo?.preserveState?.timestamp) {
          const isStale = Date.now() - returnTo.preserveState.timestamp > 3600000
          if (isStale) {
            return '/sources'
          }
        }

        return returnTo?.path || '/sources'
      },

      getReturnLabel: () => {
        const returnTo = get().returnTo

        // Check if context is stale (older than 1 hour)
        if (returnTo?.preserveState?.timestamp) {
          const isStale = Date.now() - returnTo.preserveState.timestamp > 3600000
          if (isStale) {
            return 'Back to Sources'
          }
        }

        return returnTo?.label || 'Back to Sources'
      }
    }),
    {
      name: 'navigation-storage',
      skipHydration: true,
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true)
      },
      storage: {
        getItem: (name: string) => {
          try {
            const value = sessionStorage.getItem(name)
            return value
          } catch {
            return null
          }
        },
        setItem: (name: string, value: string) => {
          try {
            sessionStorage.setItem(name, value)
          } catch {
            // Silently fail if sessionStorage is not available
          }
        },
        removeItem: (name: string) => {
          try {
            sessionStorage.removeItem(name)
          } catch {
            // Silently fail if sessionStorage is not available
          }
        }
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any
    }
  )
)
