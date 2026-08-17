import type { Chapter } from '@/lib/types/course'

export function canRequestChapterReview(chapter: Chapter | undefined): boolean {
  if (!chapter || chapter.status !== 'reviewing') return false
  return chapter.review_status === 'pending' || chapter.validation_status === 'pending'
}
