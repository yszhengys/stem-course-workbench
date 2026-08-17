import { useEffect } from 'react'
import { useNavigationStore } from '@/lib/stores/navigation-store'

export function useNavigation() {
  const store = useNavigationStore()

  // skipHydration is set, so rehydrate from sessionStorage explicitly after
  // mount — the first render matches SSR (defaults), then the per-tab value
  // applies without a hydration mismatch.
  useEffect(() => {
    void useNavigationStore.persist.rehydrate()
  }, [])

  return {
    setReturnTo: store.setReturnTo,
    clearReturnTo: store.clearReturnTo,
    getReturnPath: store.getReturnPath,
    getReturnLabel: store.getReturnLabel,
    returnTo: store.returnTo,
    hasHydrated: store.hasHydrated,
  }
}
