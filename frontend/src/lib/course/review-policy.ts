import type { Chapter } from '@/lib/types/course'

const REVIEWABLE_CHAPTER_STATES = new Set(['generating', 'reviewing', 'blocked', 'ready'])

export function canRequestChapterReview(chapter: Chapter | undefined): boolean {
  return Boolean(chapter && REVIEWABLE_CHAPTER_STATES.has(chapter.status))
}
