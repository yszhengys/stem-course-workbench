import { describe, expect, it } from 'vitest'

import { canRequestChapterReview } from './review-policy'
import type { Chapter } from '@/lib/types/course'

function chapter(overrides: Partial<Chapter>): Chapter {
  return {
    id: 'course_chapter_version:one',
    course_version: 'course_outline_version:one',
    chapter_no: 1,
    title: 'Limits',
    chapter_key: 'limits',
    version_no: 1,
    artifact: null,
    input_hash: null,
    status: 'reviewing',
    published_at: null,
    content: null,
    review_status: 'pending',
    validation_status: 'pending',
    citations: null,
    created: null,
    updated: null,
    ...overrides,
  }
}

describe('canRequestChapterReview', () => {
  it('allows only a reviewing chapter with pending review or validation work', () => {
    expect(canRequestChapterReview(chapter({}))).toBe(true)
    expect(canRequestChapterReview(chapter({ review_status: 'passed' }))).toBe(true)
    expect(canRequestChapterReview(chapter({ status: 'ready' }))).toBe(false)
    expect(canRequestChapterReview(chapter({ status: 'published' }))).toBe(false)
    expect(canRequestChapterReview(chapter({ review_status: 'passed', validation_status: 'passed' }))).toBe(false)
  })
})
