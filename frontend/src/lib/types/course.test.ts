import { describe, expect, it } from 'vitest'

import { chapterArtifactSchema, courseOutlineArtifactSchema } from './course'

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
    const { provenance: _provenance, ...formula } = payload.formulas[0]
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
