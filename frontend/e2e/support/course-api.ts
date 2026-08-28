import type { Page, Route } from '@playwright/test'

import {
  courseExerciseGradeResponseSchema,
  courseExerciseHintResponseSchema,
  courseExerciseRevealResponseSchema,
  courseExerciseSchema,
  courseLabSchema,
  courseLearnerChapterResponseSchema,
  courseLearnerNotesResponseSchema,
  courseLearnerSourcesResponseSchema,
  courseLearningEventResponseSchema,
  courseLearningOverviewSchema,
  courseModelOptionsSchema,
  courseSchema,
} from '../../src/lib/types/course'

export const COURSE_ID = 'course:a11y'
export const COURSE_PATH_ID = 'course%3Aa11y'
export const CHAPTER_KEY = 'vectors'
export const SNAPSHOT_TOKEN = 'a'.repeat(64)
const EXERCISE_SNAPSHOT = 'b'.repeat(64)
const NOW = '2026-08-29T08:00:00.000Z'

const COURSE = courseSchema.parse({
  id: COURSE_ID,
  title: 'Accessible vector mechanics',
  notebook: 'notebook:a11y',
  subject: 'physics',
  description: 'A source-grounded browser-test course.',
  language: 'en-US',
  status: 'ready',
  source_ids: ['source:a11y'],
  primary_source_ids: ['source:a11y'],
  supplement_source_ids: [],
  outline_version_id: 'course_version:published',
  error_message: null,
  outline: null,
  config: null,
  created: NOW,
  updated: NOW,
})

const OVERVIEW = courseLearningOverviewSchema.parse({
  course_id: COURSE_ID,
  course_version_id: 'course_version:published',
  chapters: [
    {
      chapter_key: CHAPTER_KEY,
      chapter_no: 1,
      title: 'Vectors',
      snapshot_token: SNAPSHOT_TOKEN,
      latest_position: null,
    },
  ],
  concepts: [{ key: 'vector-components', label: 'Vector components' }],
  masteries: [],
  review_queue: [],
})

const CHAPTER = courseLearnerChapterResponseSchema.parse({
  course_id: COURSE_ID,
  course_version_id: 'course_version:published',
  chapter_key: CHAPTER_KEY,
  chapter_no: 1,
  title: 'Vectors',
  status: 'published',
  snapshot_token: SNAPSHOT_TOKEN,
  artifact: {
    purpose: 'Resolve physical vectors into measurable components.',
    prerequisites: ['Signed numbers'],
    objectives: ['Resolve a vector along a chosen axis.'],
    sections: [
      {
        block_key: 'vector-components',
        title: 'Vector components',
        markdown: 'A component is the projection of a vector onto an axis.',
        anchor_ids: ['anchor:vector'],
        provenance: 'verbatim',
      },
    ],
    definitions: ['A vector has magnitude and direction.'],
    formulas: [
      {
        key: 'velocity-law',
        latex: 'v = v_0 + at',
        meaning: 'Velocity under constant acceleration.',
        anchor_ids: ['anchor:vector'],
        unit_expression: 'm/s',
        provenance: 'adapted',
      },
    ],
    worked_examples: [
      {
        key: 'worked-velocity',
        prompt: 'Find the velocity after two seconds.',
        steps: ['Substitute the known values.', 'Evaluate the expression.'],
        answer: '12 m/s',
        anchor_ids: ['anchor:vector'],
        unit_expression: 'm/s',
        provenance: 'adapted',
      },
    ],
    misconceptions: ['A negative component does not mean a negative magnitude.'],
    pitfalls: ['Keep the reference axis fixed.'],
    quick_reference: ['Choose axes before resolving components.'],
    citations: ['anchor:vector'],
  },
})

const SOURCES = courseLearnerSourcesResponseSchema.parse({
  snapshot_token: SNAPSHOT_TOKEN,
  sources: [
    {
      anchor_id: 'anchor:vector',
      filename: 'stem-evidence-gold.pdf',
      kind: 'pdf_page',
      index: 1,
      quote: 'v(t) = v0 + a*t; worked answer: 12 m/s',
      source_role: 'PRIMARY',
      bbox: [0.08, 0.12, 0.84, 0.31],
    },
  ],
})

const NOTES = courseLearnerNotesResponseSchema.parse({
  snapshot_token: SNAPSHOT_TOKEN,
  notes: [],
})

const EXERCISES = courseExerciseSchema.array().parse([
  {
    key: 'vector-core',
    chapter_key: CHAPTER_KEY,
    prompt: 'What is the velocity after two seconds?',
    concept_keys: ['vector-components'],
    exercise_type: 'generated_core',
    answer_type: 'numeric',
    answer_format: {
      kind: 'numeric',
      component_count: null,
      unit_required: false,
      parts: [],
    },
    snapshot_token: EXERCISE_SNAPSHOT,
    source_anchor_ids: ['anchor:vector'],
    source_number: '1',
    source_section: 'Worked answer',
    difficulty: {
      concept_count: 1,
      reasoning_steps: 2,
      symbolic_depth: 1,
      representation_shifts: 0,
      proof_burden: 0,
      physics_constraints: 1,
    },
    is_core: true,
    is_gating: true,
    is_source_level: true,
    verification: {
      level: 'L2',
      method: 'deterministic_solver',
      anchor_ids: [],
      reason: 'Independent numeric oracle: 8 + 2 * 2 = 12.',
      verified_at: NOW,
    },
    learning_blocked_reason: null,
    transfer: null,
  },
])

const LABS = courseLabSchema.array().parse([
  {
    id: 'course_lab:a11y',
    lab_key: 'slope-lab',
    lab_type: 'function_plot',
    spec: {
      kind: 'function_plot',
      key: 'slope-lab',
      title: 'Slope explorer',
      anchor_ids: ['anchor:vector'],
      provenance: 'adapted',
      expressions: ['m*x'],
      domain: { x: [-2, 2] },
      controls: [
        { key: 'm', label: 'Slope', min: -2, max: 2, value: 1, step: 1 },
      ],
      objects: [],
    },
    proposal_hash: 'c'.repeat(64),
    approved_hash: 'c'.repeat(64),
    approved_at: NOW,
    approval_reason: 'Checked against the source-grounded objective.',
  },
])

const MODEL_OPTIONS = courseModelOptionsSchema.parse({
  defaults: {},
  options: [],
})

function requestBody(route: Route): Record<string, unknown> {
  const body = route.request().postDataJSON()
  if (body === null || typeof body !== 'object' || Array.isArray(body)) return {}
  return body as Record<string, unknown>
}

async function respond(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

export interface CourseApiFixtureState {
  createRequests: Record<string, unknown>[]
  unknownRequests: string[]
}

export async function installCourseApi(page: Page): Promise<CourseApiFixtureState> {
  const state: CourseApiFixtureState = { createRequests: [], unknownRequests: [] }
  let eventCounter = 0

  await page.addInitScript(() => {
    window.localStorage.setItem('i18nextLng', 'en-US')
    window.localStorage.setItem('auth-storage', JSON.stringify({
      state: { isAuthenticated: true, token: 'not-required' },
      version: 0,
    }))
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const method = request.method()
    const url = new URL(request.url())
    const path = decodeURIComponent(url.pathname)
    const signature = `${method} ${path}`

    if (signature === 'GET /api/config') {
      await respond(route, {
        version: 'e2e',
        latestVersion: null,
        hasUpdate: false,
        dbStatus: 'online',
      })
      return
    }
    if (signature === 'GET /api/auth/status') {
      await respond(route, { auth_enabled: false })
      return
    }
    if (signature === 'GET /api/credentials/status') {
      await respond(route, { configured: {}, source: {}, encryption_configured: true })
      return
    }
    if (signature === 'GET /api/credentials/env-status') {
      await respond(route, {})
      return
    }
    // Dashboard shell bootstrap requests made by the production command palette.
    if (
      signature === 'GET /api/notebooks'
      || signature === 'GET /api/transformations'
      || signature === 'GET /api/episode-profiles'
    ) {
      await respond(route, [])
      return
    }
    if (signature === 'GET /api/settings') {
      await respond(route, {})
      return
    }
    if (signature === 'POST /api/courses') {
      const body = requestBody(route)
      state.createRequests.push(body)
      await respond(route, courseSchema.parse({
        ...COURSE,
        id: 'course:created',
        title: typeof body.title === 'string' ? body.title : COURSE.title,
        subject: typeof body.subject === 'string' ? body.subject : null,
        description: typeof body.description === 'string' ? body.description : null,
        language: typeof body.language === 'string' ? body.language : 'en-US',
      }))
      return
    }
    if (signature === `GET /api/courses/${COURSE_ID}`) {
      await respond(route, COURSE)
      return
    }
    if (signature === 'GET /api/courses/model-options') {
      await respond(route, MODEL_OPTIONS)
      return
    }
    if (signature === `GET /api/courses/${COURSE_ID}/learning/overview`) {
      await respond(route, OVERVIEW)
      return
    }
    if (signature === `GET /api/courses/${COURSE_ID}/learning/chapters/${CHAPTER_KEY}`) {
      await respond(route, CHAPTER)
      return
    }
    if (signature === `GET /api/courses/${COURSE_ID}/learning/chapters/${CHAPTER_KEY}/sources`) {
      await respond(route, SOURCES)
      return
    }
    if (signature === `GET /api/courses/${COURSE_ID}/learning/chapters/${CHAPTER_KEY}/notes`) {
      await respond(route, NOTES)
      return
    }
    if (signature === `GET /api/courses/${COURSE_ID}/exercises`) {
      await respond(route, EXERCISES)
      return
    }
    if (signature === `GET /api/courses/${COURSE_ID}/chapters/${CHAPTER_KEY}/labs`) {
      await respond(route, LABS)
      return
    }
    if (signature === `GET /api/courses/${COURSE_ID}/tutor/sessions`) {
      await respond(route, [])
      return
    }
    if (signature === `POST /api/courses/${COURSE_ID}/learning/events`) {
      const body = requestBody(route)
      const kind = body.kind === 'reading_position' ? 'reading_position' : 'chapter_opened'
      eventCounter += 1
      await respond(route, courseLearningEventResponseSchema.parse({
        event: {
          event_id: `activity-${eventCounter}`,
          course_id: COURSE_ID,
          course_version_id: 'course_version:published',
          chapter_key: CHAPTER_KEY,
          concept_key: null,
          exercise_key: null,
          kind,
          payload: kind === 'reading_position'
            ? { block_key: body.payload && typeof body.payload === 'object'
                ? (body.payload as Record<string, unknown>).block_key
                : 'vector-components' }
            : { block_key: null },
          occurred_at: NOW,
        },
        mastery: null,
      }))
      return
    }
    if (signature === `POST /api/courses/${COURSE_ID}/exercises/vector-core/hints/next`) {
      const body = requestBody(route)
      await respond(route, courseExerciseHintResponseSchema.parse({
        snapshot_token: EXERCISE_SNAPSHOT,
        hint_index: 1,
        total_hints: 2,
        hint: 'Substitute the initial velocity, acceleration, and time.',
        event: {
          event_id: 'hint-event',
          course_id: COURSE_ID,
          course_version_id: 'course_version:published',
          chapter_key: CHAPTER_KEY,
          concept_key: null,
          exercise_key: 'vector-core',
          kind: 'hint_viewed',
          payload: { attempt_key: body.attempt_key, hint_index: 1 },
          occurred_at: NOW,
        },
        mastery: null,
      }))
      return
    }
    if (signature === `POST /api/courses/${COURSE_ID}/exercises/vector-core/reveal`) {
      const body = requestBody(route)
      await respond(route, courseExerciseRevealResponseSchema.parse({
        snapshot_token: EXERCISE_SNAPSHOT,
        answer: '12',
        transfer: null,
        events: [
          {
            event_id: 'reveal-event',
            course_id: COURSE_ID,
            course_version_id: 'course_version:published',
            chapter_key: CHAPTER_KEY,
            concept_key: 'vector-components',
            exercise_key: 'vector-core',
            kind: 'answer_revealed',
            payload: { attempt_key: body.attempt_key, transfer_task_key: null },
            occurred_at: NOW,
          },
        ],
        mastery: null,
      }))
      return
    }
    if (signature === `POST /api/courses/${COURSE_ID}/exercises/vector-core/grade`) {
      await respond(route, courseExerciseGradeResponseSchema.parse({
        grade: {
          correct: true,
          advisory: false,
          grants_mastery: true,
          feedback_code: 'correct',
          part_results: [],
        },
        mastery: null,
        event_key: 'grade-event',
        snapshot_token: EXERCISE_SNAPSHOT,
      }))
      return
    }

    state.unknownRequests.push(`${signature}${url.search}`)
    await respond(route, { detail: `No browser fixture for ${signature}` }, 404)
  })

  return state
}
