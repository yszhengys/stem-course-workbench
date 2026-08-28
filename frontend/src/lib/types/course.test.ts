import { describe, expect, it } from 'vitest'

import {
  academicVerificationSchema,
  chapterArtifactSchema,
  courseAnswerFormatSchema,
  courseExerciseGradeResponseSchema,
  courseExerciseGradeRequestSchema,
  courseExerciseHintRequestSchema,
  courseExerciseHintResponseSchema,
  courseExerciseRevealRequestSchema,
  courseExerciseRevealResponseSchema,
  courseExerciseSchema,
  courseDraftOperationRequestSchema,
  courseDraftResponseSchema,
  courseLearnerChapterResponseSchema,
  courseLearnerNoteCreateRequestSchema,
  courseLearnerNotesResponseSchema,
  courseLearnerSourcesResponseSchema,
  courseLearningEventRequestSchema,
  courseLearningOverviewSchema,
  courseOutlineArtifactSchema,
  courseTransferGradeRequestSchema,
  courseTutorMessageRequestSchema,
  courseTutorMessageResponseSchema,
  courseTutorSessionSchema,
  gradeResultSchema,
  exerciseVerificationSchema,
  learningEventSchema,
} from './course'

const academicArtifactHash = 'a'.repeat(64)

describe('academicVerificationSchema', () => {
  it.each([
    { level: 'L0', method: 'structure', anchor_ids: [], reason: null, verified_at: null, artifact_hash: null },
    { level: 'L1', method: 'self_consistency', anchor_ids: [], reason: null, verified_at: null, artifact_hash: null },
    {
      level: 'L2', method: 'source_answer', anchor_ids: ['anchor:answer_key'],
      reason: null, verified_at: null, artifact_hash: academicArtifactHash,
    },
    {
      level: 'L2', method: 'deterministic_solver', anchor_ids: [],
      reason: 'SymPy reproduced the answer.', verified_at: null, artifact_hash: academicArtifactHash,
    },
    {
      level: 'L3', method: 'human_review', anchor_ids: ['anchor:answer_key'],
      reason: 'Checked line by line.', verified_at: '2026-08-29T00:00:00Z',
      artifact_hash: academicArtifactHash,
    },
  ])('accepts supported verification %#', (verification) => {
    expect(academicVerificationSchema.parse(verification).level).toBe(verification.level)
  })

  it.each([
    { level: 'L2', method: 'source_answer', anchor_ids: [], reason: null, verified_at: null, artifact_hash: null },
    {
      level: 'L3', method: 'human_review', anchor_ids: [], reason: 'Checked.',
      verified_at: '2026-08-29T00:00:00Z', artifact_hash: academicArtifactHash,
    },
    {
      level: 'L3', method: 'human_review', anchor_ids: ['anchor:answer_key'], reason: null,
      verified_at: '2026-08-29T00:00:00Z', artifact_hash: academicArtifactHash,
    },
    {
      level: 'L3', method: 'human_review', anchor_ids: ['anchor:answer_key'], reason: 'Checked.',
      verified_at: null, artifact_hash: academicArtifactHash,
    },
    {
      level: 'L3', method: 'human_review', anchor_ids: ['anchor:answer_key'], reason: 'Checked.',
      verified_at: '2026-08-29T00:00:00Z', artifact_hash: null,
    },
  ])('rejects unsupported verification %#', (verification) => {
    expect(() => academicVerificationSchema.parse(verification)).toThrow()
  })

  it('adds an explicit L1 default to legacy chapter artifacts', () => {
    const parsed = chapterArtifactSchema.parse(chapterPayload())

    expect(parsed.formulas[0].verification).toMatchObject({ level: 'L1', method: 'self_consistency' })
    expect(parsed.worked_examples[0].verification).toMatchObject({ level: 'L1', method: 'self_consistency' })
    expect(parsed.exercises[0].verification).toMatchObject({ level: 'L1', method: 'self_consistency' })
  })
})

const attribution = (provenance: string, anchorIds: string[] = []) => ({
  provenance,
  anchor_ids: anchorIds,
})

const chapterPayload = () => ({
  chapter_key: 'limits',
  purpose: 'Understand limits.',
  prerequisites: ['Algebra'],
  objectives: ['Evaluate a limit'],
  sections: [{
    key: 'definition',
    title: 'Definition',
    markdown: 'A grounded definition.',
    anchor_ids: ['anchor:one'],
    provenance: 'verbatim',
  }],
  definitions: ['Limit'],
  formulas: [{
    key: 'square',
    latex: 'x^2',
    meaning: 'Square',
    anchor_ids: [],
    unit_expression: null,
    oracle_unit_expression: null,
    provenance: 'derived',
    oracle_expression: 'x^2',
    oracle_substitutions: {},
  }],
  worked_examples: [{
    key: 'example',
    prompt: 'Compute.',
    steps: ['Substitute.'],
    answer: '4',
    anchor_ids: ['anchor:one'],
    oracle_expression: '2 + 2',
    oracle_values: {},
    oracle_answer: 4,
    unit_expression: null,
    oracle_unit_expression: null,
    provenance: 'adapted',
  }],
  labs: [{
    kind: 'function_plot',
    key: 'limit-plot',
    title: 'Limit plot',
    anchor_ids: [],
    provenance: 'pedagogical',
    expressions: ['x^2'],
    domain: { x: [-2, 2] },
    controls: [],
    objects: [],
  }],
  misconceptions: [],
  pitfalls: ['Do not substitute across a discontinuity.'],
  exercises: [{
    key: 'core',
    prompt: 'Evaluate.',
    difficulty: 'core',
    hints: ['h1', 'h2', 'h3', 'h4'],
    answer: '2',
    transfer_task: 'Transfer.',
    anchor_ids: [],
    oracle_expression: null,
    oracle_values: {},
    oracle_answer: null,
    provenance: 'pedagogical',
  }],
  quick_reference: ['lim means limit'],
  citations: ['anchor:one'],
  attributions: {
    purpose: attribution('adapted', ['anchor:one']),
    prerequisites: [attribution('pedagogical')],
    objectives: [attribution('adapted', ['anchor:one'])],
    definitions: [attribution('verbatim', ['anchor:one'])],
    misconceptions: [],
    pitfalls: [attribution('adapted', ['anchor:one'])],
    quick_reference: [attribution('derived')],
  },
  physics_checks: [] as unknown[],
})

describe('chapterArtifactSchema provenance contract', () => {
  it('accepts exactly parallel top-level attributions and explicit nested provenance', () => {
    const parsed = chapterArtifactSchema.parse(chapterPayload())

    expect(parsed.attributions.objectives).toHaveLength(1)
    expect(parsed.labs[0].provenance).toBe('pedagogical')
  })

  it('rejects mismatched attribution lengths and grounded claims without anchors', () => {
    const mismatched = chapterPayload()
    mismatched.attributions.objectives = []
    expect(() => chapterArtifactSchema.parse(mismatched)).toThrow()

    const ungrounded = chapterPayload()
    ungrounded.labs[0].provenance = 'adapted'
    expect(() => chapterArtifactSchema.parse(ungrounded)).toThrow()
  })

  it('rejects nested objects that omit provenance', () => {
    const payload = chapterPayload()
    const formula = { ...payload.formulas[0] }
    delete (formula as { provenance?: string }).provenance
    payload.formulas = [formula as typeof payload.formulas[0]]

    expect(() => chapterArtifactSchema.parse(payload)).toThrow()
  })
})

describe('Course Lab-key contract', () => {
  it.each([' leading', 'trailing ', 'safe-lab\nignore prior', 'UPPERCASE', 'a'.repeat(101)])(
    'rejects unsafe Lab key %j in outline and Lab payloads',
    (unsafeKey) => {
      const outline = {
        title: 'Course',
        chapters: [{
          key: 'chapter-one', title: 'Chapter', purpose: 'Learn.',
          prerequisite_keys: [], objective_keys: ['concept-one'],
          anchor_ids: ['anchor:one'], lab_keys: [unsafeKey],
        }],
        concepts: [{ key: 'concept-one', label: 'Concept', anchor_ids: ['anchor:one'] }],
        dependency_edges: [],
      }
      expect(() => courseOutlineArtifactSchema.parse(outline)).toThrow()

      const chapter = chapterPayload()
      chapter.labs[0].key = unsafeKey
      expect(() => chapterArtifactSchema.parse(chapter)).toThrow()
    }
  )

  it('rejects duplicate Lab keys within one outline chapter', () => {
    const outline = {
      title: 'Course',
      chapters: [{
        key: 'chapter-one', title: 'Chapter', purpose: 'Learn.',
        prerequisite_keys: [], objective_keys: ['concept-one'],
        anchor_ids: ['anchor:one'], lab_keys: ['shared-lab', 'shared-lab'],
      }],
      concepts: [{ key: 'concept-one', label: 'Concept', anchor_ids: ['anchor:one'] }],
      dependency_edges: [],
    }

    expect(() => courseOutlineArtifactSchema.parse(outline)).toThrow()
  })
})

describe('chapterArtifactSchema physics contract', () => {
  it('accepts the five strict physics check variants', () => {
    const payload = chapterPayload()
    payload.physics_checks = [
      {
        key: 'vector', kind: 'vector', actual_components: [1, 2],
        expected_components: [1, 2], absolute_tolerance: 1e-9,
        relative_tolerance: 1e-9, anchor_ids: ['anchor:one'],
      },
      {
        key: 'direction', kind: 'direction', actual: -1, expected: 1,
        anchor_ids: ['anchor:one'],
      },
      {
        key: 'frame', kind: 'reference_frame', actual: 'ground', expected: 'train',
        anchor_ids: ['anchor:one'],
      },
      {
        key: 'boundary', kind: 'boundary', value: 0, minimum: -1, maximum: 1,
        anchor_ids: ['anchor:one'],
      },
      {
        key: 'limit', kind: 'limit', expression: 'sin(x)/x', variable: 'x',
        point: 0, expected: 1, side: 'both', anchor_ids: ['anchor:one'],
      },
    ]

    expect(chapterArtifactSchema.parse(payload).physics_checks).toHaveLength(5)
  })

  it.each([
    { key: 'unknown', kind: 'unknown', anchor_ids: ['anchor:one'] },
    {
      key: 'vector', kind: 'vector', actual_components: [1, 2],
      expected_components: [1, 2, 3], absolute_tolerance: 1e-9,
      relative_tolerance: 1e-9, anchor_ids: ['anchor:one'],
    },
    {
      key: 'direction', kind: 'direction', actual: 2, expected: 1,
      anchor_ids: ['anchor:one'],
    },
    {
      key: 'boundary', kind: 'boundary', value: 0, minimum: 2, maximum: 1,
      anchor_ids: ['anchor:one'],
    },
    {
      key: 'limit', kind: 'limit', expression: '__import__(os)', variable: 'x',
      point: 0, expected: 1, side: 'both', anchor_ids: ['anchor:one'],
    },
    {
      key: 'direction', kind: 'direction', actual: 1, expected: 1,
      anchor_ids: ['anchor:one'], extra: 'forbidden',
    },
  ])('rejects malformed physics check %#', (physicsCheck) => {
    const payload = chapterPayload()
    payload.physics_checks = [physicsCheck]

    expect(() => chapterArtifactSchema.parse(payload)).toThrow()
  })
})

const difficultyVector = {
  concept_count: 1,
  reasoning_steps: 2,
  symbolic_depth: 1,
  representation_shifts: 0,
  proof_burden: 0,
  physics_constraints: 0,
}

const learnerExercise = () => ({
  key: 'limits-core',
  chapter_key: 'limits',
  prompt: 'Evaluate the limit.',
  concept_keys: ['limit-laws'],
  exercise_type: 'generated_core',
  answer_type: 'numeric',
  answer_format: {
    kind: 'numeric', component_count: null, unit_required: false, parts: [],
  },
  snapshot_token: 'a'.repeat(64),
  source_anchor_ids: ['anchor:limits'],
  source_number: '4.1',
  source_section: 'Limits',
  difficulty: difficultyVector,
  is_core: true,
  is_gating: true,
  is_source_level: true,
  verification: {
    level: 'L2',
    method: 'deterministic_solver',
    anchor_ids: [],
    reason: 'Deterministic answer check transcript sha256:abc',
    verified_at: null,
  },
  learning_blocked_reason: null,
  transfer: {
    key: 'limits-transfer',
    prompt: 'Apply the same invariant to a graph.',
    invariant_concept_keys: ['limit-laws'],
    dimensions: ['representation'],
    answer_type: 'numeric',
    answer_format: {
      kind: 'numeric', component_count: null, unit_required: false, parts: [],
    },
    difficulty: { ...difficultyVector, representation_shifts: 1 },
    anchor_ids: ['anchor:limits'],
  },
})

describe('learner-safe Course V2 schemas', () => {
  it('validates exercise verification provenance independently of mastery claims', () => {
    expect(exerciseVerificationSchema.parse({
      level: 'L2',
      method: 'source_answer',
      anchor_ids: ['anchor:answer'],
      reason: null,
      verified_at: null,
    }).level).toBe('L2')

    expect(() => exerciseVerificationSchema.parse({
      level: 'L1',
      method: 'independent_model_review',
      anchor_ids: [],
      reason: null,
      verified_at: '2026-08-28T00:00:00Z',
    })).toThrow()
    expect(() => exerciseVerificationSchema.parse({
      level: 'L3',
      method: 'human_review',
      anchor_ids: [],
      reason: null,
      verified_at: '2026-08-28T00:00:00Z',
    })).toThrow()
  })

  it('accepts learner exercise metadata but rejects hidden grading material', () => {
    const parsed = courseExerciseSchema.parse(learnerExercise())
    expect(parsed.key).toBe('limits-core')
    expect(parsed.verification.level).toBe('L2')
    expect(parsed.learning_blocked_reason).toBeNull()

    expect(() => courseExerciseSchema.parse({
      ...learnerExercise(),
      grader: { kind: 'numeric', oracle_answer: 4 },
    })).toThrow()
    expect(() => courseExerciseSchema.parse({
      ...learnerExercise(),
      transfer: {
        ...learnerExercise().transfer,
        change_evidence: 'Do not expose the generation rationale.',
        grader: { kind: 'numeric', oracle_answer: 4 },
      },
    })).toThrow()
  })

  it('requires an explicit block reason for a legacy L1 learning exercise', () => {
    const legacy = {
      ...learnerExercise(),
      verification: {
        level: 'L1',
        method: 'independent_model_review',
        anchor_ids: [],
        reason: null,
        verified_at: null,
      },
      learning_blocked_reason: 'verification_required',
    }

    expect(courseExerciseSchema.parse(legacy).learning_blocked_reason).toBe(
      'verification_required'
    )
  })

  it('requires current-version record IDs and strict learning overview fields', () => {
    const overview = {
      course_id: 'course:abc',
      course_version_id: 'course_version:published',
      chapters: [{
        chapter_key: 'limits',
        chapter_no: 1,
        title: 'Limits',
        snapshot_token: 'a'.repeat(64),
        latest_position: null,
      }],
      concepts: [{ key: 'limit-laws', label: 'Limit laws' }],
      masteries: [],
      review_queue: [],
    }

    expect(courseLearningOverviewSchema.parse(overview).course_version_id)
      .toBe('course_version:published')
    expect(() => courseLearningOverviewSchema.parse({
      ...overview,
      internal_snapshot: 'forbidden',
    })).toThrow()
    expect(() => courseLearningOverviewSchema.parse({
      ...overview,
      course_version_id: 'published',
    })).toThrow()
    expect(() => courseLearningOverviewSchema.parse({
      ...overview,
      course_id: 'notebook:abc',
    })).toThrow()
    expect(() => courseLearningOverviewSchema.parse({
      ...overview,
      course_version_id: 'chapter:published',
    })).toThrow()
  })

  it('parses recursive grade feedback without accepting an answer oracle', () => {
    const response = {
      grade: {
        correct: true,
        advisory: false,
        grants_mastery: true,
        feedback_code: 'correct',
        part_results: [{
          correct: true,
          advisory: false,
          grants_mastery: true,
          feedback_code: 'correct',
          part_results: [],
        }],
      },
      mastery: null,
      event_key: 'grade-limits-one',
      snapshot_token: 'a'.repeat(64),
    }

    expect(courseExerciseGradeResponseSchema.parse(response).grade.part_results)
      .toHaveLength(1)
    expect(() => courseExerciseGradeResponseSchema.parse({
      ...response,
      oracle_answer: 4,
    })).toThrow()
  })

  it('rejects contradictory recursive grade outcomes', () => {
    expect(() => gradeResultSchema.parse({
      correct: true,
      advisory: false,
      grants_mastery: false,
      feedback_code: 'correct',
      part_results: [],
    })).toThrow()
    expect(() => gradeResultSchema.parse({
      correct: true,
      advisory: false,
      grants_mastery: true,
      feedback_code: 'correct',
      part_results: [{
        correct: false,
        advisory: false,
        grants_mastery: false,
        feedback_code: 'incorrect',
        part_results: [],
      }],
    })).toThrow()
  })

  it.each([
    { kind: 'numeric', component_count: null, unit_required: false, parts: [] },
    { kind: 'symbolic', component_count: null, unit_required: false, parts: [] },
    { kind: 'unit', component_count: null, unit_required: true, parts: [] },
    { kind: 'vector', component_count: 3, unit_required: true, parts: [] },
    { kind: 'set', component_count: null, unit_required: false, parts: [] },
    {
      kind: 'multipart', component_count: null, unit_required: false,
      parts: [
        { kind: 'numeric', component_count: null, unit_required: false, parts: [] },
        { kind: 'unit', component_count: null, unit_required: true, parts: [] },
      ],
    },
    { kind: 'proof', component_count: null, unit_required: false, parts: [] },
    { kind: 'explanation', component_count: null, unit_required: false, parts: [] },
  ])('accepts the learner-safe $kind answer shape', (answerFormat) => {
    expect(courseAnswerFormatSchema.parse(answerFormat).kind).toBe(answerFormat.kind)
  })

  it('rejects answer-shape metadata that leaks or contradicts its kind', () => {
    expect(() => courseAnswerFormatSchema.parse({
      kind: 'numeric', component_count: null, unit_required: false, parts: [],
      expected_value: 4,
    })).toThrow()
    expect(() => courseAnswerFormatSchema.parse({
      kind: 'vector', component_count: null, unit_required: false, parts: [],
    })).toThrow()
    expect(() => courseAnswerFormatSchema.parse({
      kind: 'numeric', component_count: 2, unit_required: false, parts: [],
    })).toThrow()
  })

  it('parses a learner chapter projection and rejects every author-only field', () => {
    const response = {
      course_id: 'course:abc',
      course_version_id: 'course_version:published',
      chapter_key: 'limits',
      chapter_no: 1,
      title: 'Limits',
      status: 'published',
      snapshot_token: 'a'.repeat(64),
      artifact: {
        purpose: 'Understand limits.',
        prerequisites: ['Algebra'],
        objectives: ['Evaluate limits'],
        sections: [{
          block_key: 'definition', title: 'Definition', markdown: 'Grounded.',
          anchor_ids: ['anchor:one'], provenance: 'adapted',
        }],
        definitions: ['Limit'],
        formulas: [],
        worked_examples: [],
        misconceptions: [],
        pitfalls: [],
        quick_reference: [],
        citations: ['anchor:one'],
      },
    }
    expect(courseLearnerChapterResponseSchema.parse(response).artifact.sections[0].block_key)
      .toBe('definition')

    for (const forbidden of ['exercises', 'attributions', 'physics_checks', 'labs']) {
      expect(() => courseLearnerChapterResponseSchema.parse({
        ...response,
        artifact: { ...response.artifact, [forbidden]: [] },
      })).toThrow()
    }
    expect(() => courseLearnerChapterResponseSchema.parse({
      ...response,
      artifact: {
        ...response.artifact,
        formulas: [{
          key: 'formula', latex: 'x', meaning: 'x', anchor_ids: [],
          unit_expression: null, provenance: 'derived', oracle_expression: 'x',
        }],
      },
    })).toThrow()
  })

  it('allows only snapshot-bound activity events on the public event route', () => {
    expect(courseLearningEventRequestSchema.parse({
      snapshot_token: 'a'.repeat(64),
      idempotency_key: 'open-one',
      chapter_key: 'limits',
      kind: 'chapter_opened',
      payload: { block_key: null },
    }).kind).toBe('chapter_opened')

    expect(() => courseLearningEventRequestSchema.parse({
      snapshot_token: 'a'.repeat(64),
      idempotency_key: 'hint-one',
      chapter_key: 'limits',
      concept_key: 'limit-laws',
      exercise_key: 'limits-core',
      kind: 'hint_viewed',
      payload: { attempt_key: 'attempt-one', hint_index: 1 },
    })).toThrow()
    expect(() => courseLearningEventRequestSchema.parse({
      idempotency_key: 'position-one', chapter_key: 'limits',
      kind: 'reading_position', payload: { block_key: 'definition' },
    })).toThrow()
  })

  it('strictly validates every learner exercise action request', () => {
    const base = {
      snapshot_token: 'a'.repeat(64), chapter_key: 'limits',
      concept_key: 'limit-laws', attempt_key: 'attempt-one',
    }
    expect(courseExerciseGradeRequestSchema.parse({
      ...base, answer: { value: '4' }, hints_used: 1,
      answer_revealed: false, mode: 'practice',
    }).answer).toEqual({ value: '4' })
    expect(courseExerciseHintRequestSchema.parse({
      ...base, idempotency_key: 'hint-one', hint_index: 1,
    }).hint_index).toBe(1)
    expect(courseExerciseRevealRequestSchema.parse({
      ...base, idempotency_key: 'reveal-one',
    }).idempotency_key).toBe('reveal-one')
    expect(courseTransferGradeRequestSchema.parse({
      ...base, source_attempt_key: 'attempt-source',
      transfer_task_key: 'limits-transfer', answer: ['4'],
    }).transfer_task_key).toBe('limits-transfer')

    for (const schema of [
      courseExerciseGradeRequestSchema,
      courseExerciseHintRequestSchema,
      courseExerciseRevealRequestSchema,
      courseTransferGradeRequestSchema,
    ]) {
      expect(() => schema.parse({ ...base, exercise_id: 'course_exercise:secret' })).toThrow()
    }
  })

  it('accepts only a single recorded hint and a server-gated reveal response', () => {
    const event = {
      event_id: 'action-one', course_id: 'course:abc',
      course_version_id: 'course_version:published', chapter_key: 'limits',
      concept_key: 'limit-laws', exercise_key: 'limits-core',
      kind: 'hint_viewed',
      payload: { attempt_key: 'attempt-one', hint_index: 1 },
      occurred_at: '2026-08-22T08:00:00Z',
    }
    expect(courseExerciseHintResponseSchema.parse({
      snapshot_token: 'a'.repeat(64), hint_index: 1, total_hints: 4,
      hint: 'Check the limiting direction.', event, mastery: null,
    }).hint).toBe('Check the limiting direction.')

    const revealed = {
      snapshot_token: 'a'.repeat(64), answer: { value: '4' }, transfer: null,
      events: [{
        ...event, event_id: 'action-reveal', kind: 'answer_revealed',
        payload: { attempt_key: 'attempt-one', transfer_task_key: null },
      }],
      mastery: null,
    }
    expect(courseExerciseRevealResponseSchema.parse(revealed).answer).toEqual({ value: '4' })
    expect(() => courseExerciseRevealResponseSchema.parse({
      ...revealed,
      grader: { kind: 'numeric', expected: 4 },
    })).toThrow()
  })

  it('parses current-chapter source metadata without accepting server paths', () => {
    const response = {
      snapshot_token: 'a'.repeat(64),
      sources: [{
        anchor_id: 'anchor:one', filename: 'calculus.pdf', kind: 'pdf_page',
        index: 4, quote: 'A source excerpt.', source_role: 'PRIMARY',
        bbox: [0.1, 0.2, 0.8, 0.4],
      }],
    }
    expect(courseLearnerSourcesResponseSchema.parse(response).sources[0].index).toBe(4)
    expect(() => courseLearnerSourcesResponseSchema.parse({
      ...response,
      sources: [{ ...response.sources[0], file_path: '/private/course.pdf' }],
    })).toThrow()
  })

  it('strictly scopes learner notes to a chapter snapshot and stable block', () => {
    const response = {
      snapshot_token: 'a'.repeat(64),
      notes: [{
        note_id: 'course_note:one', block_key: 'definition', content: 'Remember.',
        orphan_status: 'active', created: '2026-08-22T08:00:00Z',
      }],
    }
    expect(courseLearnerNotesResponseSchema.parse(response).notes[0].content)
      .toBe('Remember.')
    expect(courseLearnerNoteCreateRequestSchema.parse({
      snapshot_token: 'a'.repeat(64), block_key: 'definition', content: 'Remember.',
    }).block_key).toBe('definition')
    expect(() => courseLearnerNoteCreateRequestSchema.parse({
      snapshot_token: 'a'.repeat(64), block_key: 'definition', content: 'Remember.',
      chapter_id: 'chapter:foreign',
    })).toThrow()
  })

  it('rejects a learning response whose payload does not match its event kind', () => {
    expect(() => learningEventSchema.parse({
      event_id: 'event-one',
      course_id: 'course:abc',
      course_version_id: 'course_version:published',
      chapter_key: 'limits',
      concept_key: 'limit-laws',
      exercise_key: 'limits-core',
      kind: 'hint_viewed',
      payload: { block_key: 'definition' },
      occurred_at: '2026-08-22T08:00:00Z',
    })).toThrow()
  })

  it('strictly validates version-bound tutor sessions and responses', () => {
    const session = {
      session_id: 'course_tutor_session:one',
      course_version_id: 'course_version:published',
      chapter_key: 'limits',
      model: {
        adapter: 'open_notebook', model: 'model:teacher', reasoning_effort: null,
      },
      status: 'active',
      turns: [{
        turn_no: 1, role: 'assistant', content: 'Use the limit law [1].',
        anchor_ids: ['anchor:one'], answer_revealed: false,
      }],
      created: '2026-08-22T08:00:00Z',
    }
    const response = {
      snapshot_token: 'a'.repeat(64),
      response: {
        session_id: 'course_tutor_session:one',
        turn: session.turns[0],
        insufficient_evidence: false,
      },
    }

    expect(courseTutorSessionSchema.parse(session).status).toBe('active')
    expect(courseTutorMessageResponseSchema.parse(response).response.turn.anchor_ids)
      .toEqual(['anchor:one'])
    expect(() => courseTutorSessionSchema.parse({
      ...session, grader: { oracle_answer: 4 },
    })).toThrow()
    expect(() => courseTutorMessageResponseSchema.parse({
      ...response, debug_prompt: 'hidden system prompt',
    })).toThrow()
  })

  it('never accepts client-selected evidence or reveal scope for other intents', () => {
    const base = {
      snapshot_token: 'a'.repeat(64), idempotency_key: 'message-one',
      content: 'Explain this step.', intent: 'explain',
    }
    expect(courseTutorMessageRequestSchema.parse(base).intent).toBe('explain')
    expect(() => courseTutorMessageRequestSchema.parse({
      ...base, anchor_ids: ['anchor:foreign'],
    })).toThrow()
    expect(() => courseTutorMessageRequestSchema.parse({
      ...base, exercise_key: 'limits-core', concept_key: 'limit-laws',
      attempt_key: 'attempt-one',
    })).toThrow()
    expect(() => courseTutorMessageRequestSchema.parse({
      ...base, intent: 'reveal', exercise_key: 'limits-core',
    })).toThrow()
    expect(courseTutorMessageRequestSchema.parse({
      ...base, intent: 'hint', exercise_key: 'limits-core',
      concept_key: 'limit-laws', attempt_key: 'attempt-one',
    }).intent).toBe('hint')
    expect(courseTutorMessageRequestSchema.parse({
      ...base, intent: 'diagnose', exercise_key: 'limits-core',
      concept_key: 'limit-laws', attempt_key: 'attempt-graded',
    }).intent).toBe('diagnose')
  })

  it('strictly validates structured draft operations and revision snapshots', () => {
    const operation = {
      revision_token: 'a'.repeat(64),
      operation: {
        kind: 'replace_formula', block_key: 'speed', latex: 'v=d/t',
        anchor_ids: ['anchor:one'],
      },
    }
    expect(courseDraftOperationRequestSchema.parse(operation).operation.kind)
      .toBe('replace_formula')
    expect(courseDraftOperationRequestSchema.parse({
      ...operation,
      operation: { ...operation.operation, block_key: 'Formula 1 (legacy)' },
    }).operation.block_key).toBe('Formula 1 (legacy)')
    expect(courseDraftOperationRequestSchema.parse({
      ...operation,
      operation: {
        kind: 'replace_text',
        block_key: `worked-example-${'x'.repeat(100)}-step-50`,
        text: 'Updated step.',
        anchor_ids: [],
      },
    }).operation.block_key.length).toBeGreaterThan(100)
    expect(() => courseDraftOperationRequestSchema.parse({
      ...operation, chapter_id: 'chapter:foreign',
    })).toThrow()
    expect(() => courseDraftOperationRequestSchema.parse({
      ...operation,
      operation: { ...operation.operation, javascript: 'alert(1)' },
    })).toThrow()

    const response = {
      chapter_key: 'limits', chapter_status: 'reviewing', editable: true,
      revision_no: 1, revision_token: 'b'.repeat(64), revision_status: 'draft',
      artifact_hash: 'c'.repeat(64), artifact: chapterPayload(), exercises: [],
    }
    expect(courseDraftResponseSchema.parse(response).revision_no).toBe(1)
    expect(() => courseDraftResponseSchema.parse({
      ...response, chapter_id: 'chapter:one',
    })).toThrow()
  })
})
