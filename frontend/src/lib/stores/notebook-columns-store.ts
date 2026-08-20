import { create } from 'zustand'

interface NotebookColumnsState {
  sourcesCollapsed: boolean
  notesCollapsed: boolean
  toggleSources: () => void
  toggleNotes: () => void
  setSources: (collapsed: boolean) => void
  setNotes: (collapsed: boolean) => void
}

// Persistence moved to the on-prefs cookie (see lib/stores/prefs-sync.ts).
export const useNotebookColumnsStore = create<NotebookColumnsState>()((set) => ({
  sourcesCollapsed: false,
  notesCollapsed: false,
  toggleSources: () =>
    set((state) => ({ sourcesCollapsed: !state.sourcesCollapsed })),
  toggleNotes: () => set((state) => ({ notesCollapsed: !state.notesCollapsed })),
  setSources: (collapsed) => set({ sourcesCollapsed: collapsed }),
  setNotes: (collapsed) => set({ notesCollapsed: collapsed }),
}))
