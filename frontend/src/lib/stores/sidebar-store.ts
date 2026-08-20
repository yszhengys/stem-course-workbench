import { create } from 'zustand'

interface SidebarState {
  isCollapsed: boolean
  toggleCollapse: () => void
  setCollapsed: (collapsed: boolean) => void
}

// Persistence moved to the on-prefs cookie (see lib/stores/prefs-sync.ts):
// localStorage-only persistence caused an SSR hydration mismatch; the cookie
// lets the server and the client's first render agree.
export const useSidebarStore = create<SidebarState>()((set) => ({
  isCollapsed: false,
  toggleCollapse: () => set((state) => ({ isCollapsed: !state.isCollapsed })),
  setCollapsed: (collapsed) => set({ isCollapsed: collapsed }),
}))
