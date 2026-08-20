import { create } from 'zustand'

export type NotebookViewMode = 'tile' | 'list'

interface NotebookViewState {
  viewMode: NotebookViewMode
  setViewMode: (mode: NotebookViewMode) => void
}

// Persistence moved to the on-prefs cookie (see lib/stores/prefs-sync.ts).
export const useNotebookViewStore = create<NotebookViewState>()((set) => ({
  viewMode: 'tile',
  setViewMode: (mode) => set({ viewMode: mode }),
}))
