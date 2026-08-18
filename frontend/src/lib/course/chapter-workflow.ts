import type { CourseNote } from '@/lib/types/course'

export type ChapterProgressStatus = 'not_started' | 'in_progress' | 'completed'

const PROGRESS_TRANSITIONS: Record<ChapterProgressStatus, readonly ChapterProgressStatus[]> = {
  not_started: ['in_progress'],
  in_progress: ['not_started', 'completed'],
  completed: ['in_progress'],
}

export function canTransitionChapterProgress(
  current: string | undefined,
  target: ChapterProgressStatus,
): boolean {
  const normalized = current ?? 'not_started'
  if (!(normalized in PROGRESS_TRANSITIONS)) return false
  return PROGRESS_TRANSITIONS[normalized as ChapterProgressStatus].includes(target)
}

export function nextChapterProgressStatus(
  current: string | undefined,
): ChapterProgressStatus | null {
  const normalized = current ?? 'not_started'
  if (normalized === 'not_started' || normalized === 'completed') return 'in_progress'
  if (normalized === 'in_progress') return 'completed'
  return null
}

export function selectChapterNotes(notes: CourseNote[], chapterKey: string): CourseNote[] {
  return notes.filter((note) => note.chapter_key === chapterKey)
}
