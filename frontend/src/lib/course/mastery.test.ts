import { describe, expect, it } from 'vitest'

import {
  conceptLabel,
  selectExerciseConcept,
  selectResumeChapter,
} from './mastery'
import type { CourseExercise, CourseLearningOverview } from '@/lib/types/course'

const overview = (overrides: Partial<CourseLearningOverview> = {}): CourseLearningOverview => ({
  course_id: 'course:abc',
  course_version_id: 'course_version:published',
  chapters: [
    { chapter_key: 'limits', chapter_no: 1, title: 'Limits', snapshot_token: 'a'.repeat(64), latest_position: null },
    { chapter_key: 'vectors', chapter_no: 2, title: 'Vectors', snapshot_token: 'b'.repeat(64), latest_position: null },
  ],
  concepts: [
    { key: 'limit-laws', label: '极限定律' },
    { key: 'continuity', label: '连续性' },
  ],
  masteries: [],
  review_queue: [],
  ...overrides,
})

describe('Course mastery presentation', () => {
  it('resumes the most recently positioned published chapter', () => {
    const result = selectResumeChapter(overview({
      chapters: [
        {
          chapter_key: 'limits', chapter_no: 1, title: 'Limits',
          snapshot_token: 'a'.repeat(64),
          latest_position: {
            event_id: 'position-limits', course_id: 'course:abc',
            course_version_id: 'course_version:published', chapter_key: 'limits',
            concept_key: null, exercise_key: null, kind: 'reading_position',
            payload: { block_key: 'definition' }, occurred_at: '2026-08-21T08:00:00Z',
          },
        },
        {
          chapter_key: 'vectors', chapter_no: 2, title: 'Vectors',
          snapshot_token: 'b'.repeat(64),
          latest_position: {
            event_id: 'position-vectors', course_id: 'course:abc',
            course_version_id: 'course_version:published', chapter_key: 'vectors',
            concept_key: null, exercise_key: null, kind: 'reading_position',
            payload: { block_key: 'components' }, occurred_at: '2026-08-22T08:00:00Z',
          },
        },
      ],
    }))

    expect(result?.chapter_key).toBe('vectors')
  })

  it('prefers a due review before the first unfinished chapter', () => {
    const result = selectResumeChapter(overview({
      review_queue: [{
        chapter_key: 'vectors', concept_key: 'vector-components', status: 'review_due',
        due_at: '2026-08-22T08:00:00Z', interval_days: 3,
      }],
    }))

    expect(result?.chapter_key).toBe('vectors')
  })

  it('uses the approved outline label without exposing a stable key', () => {
    expect(conceptLabel(overview(), 'limit-laws')).toBe('极限定律')
    expect(conceptLabel(overview(), 'private-stable-key')).toBeUndefined()
  })

  it('selects the due concept for a multi-concept review exercise', () => {
    const exercise = {
      chapter_key: 'limits', concept_keys: ['continuity', 'limit-laws'],
    } as CourseExercise
    const current = overview({
      review_queue: [{
        chapter_key: 'limits', concept_key: 'limit-laws', status: 'review_due',
        due_at: '2026-08-22T08:00:00Z', interval_days: 3,
      }],
    })

    expect(selectExerciseConcept(exercise, current)).toBe('limit-laws')
  })

  it('otherwise selects the least advanced concept deterministically', () => {
    const exercise = {
      chapter_key: 'limits', concept_keys: ['limit-laws', 'continuity'],
    } as CourseExercise
    const baseMastery = {
      course_id: 'course:abc', course_version_id: 'course_version:published',
      chapter_key: 'limits', successful_exercise_keys: [], unrevealed_success_count: 0,
      review_level: 0, review_due_at: null, last_event_at: null,
      snapshot_hash: 'c'.repeat(64),
    }
    const current = overview({
      masteries: [
        { ...baseMastery, concept_key: 'limit-laws', status: 'mastered' },
        { ...baseMastery, concept_key: 'continuity', status: 'learning' },
      ],
    })

    expect(selectExerciseConcept(exercise, current)).toBe('continuity')
  })
})
