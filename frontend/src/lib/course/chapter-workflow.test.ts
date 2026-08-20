import { describe, expect, it } from 'vitest'

import type { CourseNote } from '@/lib/types/course'
import {
  canTransitionChapterProgress,
  nextChapterProgressStatus,
  selectChapterNotes,
} from './chapter-workflow'

const note = (id: string, chapterKey: string | null, orphanStatus: string): CourseNote => ({
  id,
  course: 'course:one',
  chapter: null,
  chapter_key: chapterKey,
  block_key: 'section:intro',
  orphan_status: orphanStatus,
  content: id,
})

describe('chapter workflow guards', () => {
  it('only advances progress through legal forward transitions', () => {
    expect(nextChapterProgressStatus(undefined)).toBe('in_progress')
    expect(nextChapterProgressStatus('not_started')).toBe('in_progress')
    expect(nextChapterProgressStatus('in_progress')).toBe('completed')
    expect(nextChapterProgressStatus('completed')).toBe('in_progress')
    expect(nextChapterProgressStatus('unexpected')).toBeNull()

    expect(canTransitionChapterProgress('not_started', 'completed')).toBe(false)
    expect(canTransitionChapterProgress('in_progress', 'completed')).toBe(true)
    expect(canTransitionChapterProgress('completed', 'in_progress')).toBe(true)
  })

  it('never exposes orphaned notes from a different stable chapter key', () => {
    const notes = [
      note('course_note_link:one', 'chapter-one', 'attached'),
      note('course_note_link:orphan-one', 'chapter-one', 'orphaned'),
      note('course_note_link:orphan-two', 'chapter-two', 'orphaned'),
      note('course_note_link:legacy', null, 'orphaned'),
    ]

    expect(selectChapterNotes(notes, 'chapter-one').map((item) => item.id)).toEqual([
      'course_note_link:one',
      'course_note_link:orphan-one',
    ])
  })
})
