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
  it.each(['generating', 'reviewing', 'blocked', 'ready'])(
    'allows backend-supported review state %s even after a prior review completed',
    (status) => {
      expect(canRequestChapterReview(chapter({
        status,
        review_status: 'passed',
        validation_status: 'passed',
      }))).toBe(true)
    }
  )

  it.each(['draft', 'published', 'unknown'])(
    'rejects non-reviewable chapter state %s',
    (status) => {
      expect(canRequestChapterReview(chapter({ status }))).toBe(false)
    }
  )

  it('rejects a missing chapter', () => {
    expect(canRequestChapterReview(undefined)).toBe(false)
  })
})
