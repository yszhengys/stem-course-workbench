import { z } from 'zod'

const recordId = z.string().min(3).refine((value) => value.includes(':'), {
  message: 'Expected a typed record ID',
})
const typedRecordId = (table: string) => z.string().regex(
  new RegExp(`^${table}:[^:]+$`),
  { message: `Expected a ${table} record ID` },
)
const courseRecordId = typedRecordId('course')
const courseVersionRecordId = typedRecordId('course_version')
export const sha256Schema = z.string().regex(/^[0-9a-f]{64}$/)
const timestamp = z.string().nullable().optional()
const finiteNumber = z.number().finite()
const provenanceSchema = z.enum(['verbatim', 'adapted', 'derived', 'pedagogical', '补充'])
const groundedProvenance = new Set(['verbatim', 'adapted', '补充'])

function validateProvenance(
  value: { provenance: string; anchor_ids: string[] },
  context: z.RefinementCtx,
): void {
  if (groundedProvenance.has(value.provenance) && value.anchor_ids.length === 0) {
    context.addIssue({
      code: 'custom',
      path: ['anchor_ids'],
      message: 'Grounded provenance requires at least one evidence anchor',
    })
  }
}

export const sourceRoleSchema = z.enum(['PRIMARY', 'SUPPLEMENT'])
export type SourceRole = z.infer<typeof sourceRoleSchema>

export const reasoningEffortSchema = z.enum(['low', 'medium', 'high', 'xhigh', 'max'])

function validateOpenNotebookModelId(
  selection: { adapter: string; model: string | null },
  context: z.RefinementCtx,
): void {
  if (
    selection.adapter === 'open_notebook' &&
    selection.model !== null &&
    (!selection.model.startsWith('model:') || !selection.model.slice('model:'.length).trim())
  ) {
    context.addIssue({
      code: 'custom',
      path: ['model'],
      message: 'Expected a registered model record ID',
    })
  }
}

export const modelSelectionSchema = z.object({
  adapter: z.enum(['codex_cli', 'open_notebook', 'ollama']),
  model: z.string().min(1),
  reasoning_effort: reasoningEffortSchema.nullable(),
}).strict().superRefine(validateOpenNotebookModelId)
export type ModelSelection = z.infer<typeof modelSelectionSchema>

const courseRecordDates = {
  created: timestamp,
  updated: timestamp,
}

export const courseSchema = z.object({
  id: recordId,
  title: z.string().min(1),
  notebook: recordId,
  subject: z.string().nullable(),
  description: z.string().nullable(),
  language: z.string().min(2),
  status: z.string().min(1),
  source_ids: z.array(recordId),
  primary_source_ids: z.array(recordId),
  supplement_source_ids: z.array(recordId),
  outline_version_id: recordId.nullable(),
  error_message: z.string().nullable(),
  outline: z.record(z.string(), z.unknown()).nullable(),
  config: z.record(z.string(), z.unknown()).nullable(),
  ...courseRecordDates,
}).strict()
export type Course = z.infer<typeof courseSchema>

export const conceptNodeSchema = z.object({
  key: z.string().min(1),
  label: z.string().min(1),
  anchor_ids: z.array(z.string().min(1)).min(1),
}).strict()

export const dependencyEdgeSchema = z.object({
  from_key: z.string().min(1),
  to_key: z.string().min(1),
}).strict()

const safeLabKeySchema = z.string().regex(/^[a-z0-9][a-z0-9_-]{0,99}$/)

export const outlineChapterSchema = z.object({
  key: z.string().min(1),
  title: z.string().min(1),
  purpose: z.string().min(1),
  prerequisite_keys: z.array(z.string()),
  objective_keys: z.array(z.string()).min(1),
  anchor_ids: z.array(z.string()).min(1),
  lab_keys: z.array(safeLabKeySchema).min(1),
}).strict().superRefine((chapter, context) => {
  if (new Set(chapter.lab_keys).size !== chapter.lab_keys.length) {
    context.addIssue({
      code: 'custom',
      path: ['lab_keys'],
      message: 'Lab keys must be unique within each chapter',
    })
  }
})

export const courseOutlineArtifactSchema = z.object({
  title: z.string().min(1),
  chapters: z.array(outlineChapterSchema).min(1),
  concepts: z.array(conceptNodeSchema),
  dependency_edges: z.array(dependencyEdgeSchema),
}).strict()
export type CourseOutlineArtifact = z.infer<typeof courseOutlineArtifactSchema>

export const courseVersionSchema = z.object({
  id: recordId,
  course: recordId,
  version_no: z.number().int().positive(),
  status: z.string().min(1),
  outline_hash: z.string().nullable(),
  published_at: timestamp,
  outline_artifact: courseOutlineArtifactSchema.nullable(),
  input_hash: z.string().nullable(),
  approved_at: timestamp,
  confirmation: z.string().nullable(),
  ...courseRecordDates,
}).strict()
export type CourseVersion = z.infer<typeof courseVersionSchema>

export const sourceLocatorSchema = z.object({
  source_id: recordId,
  kind: z.enum(['pdf_page', 'pptx_slide']),
  index: z.number().int().positive(),
  block_key: z.string().min(1),
  quote: z.string().min(1),
  content_sha256: z.string().regex(/^[0-9a-f]{64}$/),
  bbox: z.tuple([finiteNumber, finiteNumber, finiteNumber, finiteNumber]).nullable(),
}).strict()

export const evidenceAnchorSchema = z.object({
  id: recordId,
  course: recordId,
  source: recordId,
  evidence: recordId.nullable(),
  anchor_id: z.string().min(1),
  locator: sourceLocatorSchema,
  quote_sha256: z.string().regex(/^[0-9a-f]{64}$/),
  source_role: sourceRoleSchema,
  preview_path: z.string().nullable(),
  is_current: z.boolean(),
  ...courseRecordDates,
}).strict()
export type EvidenceAnchor = z.infer<typeof evidenceAnchorSchema>

export const labControlSchema = z.object({
  key: z.string().min(1).max(100),
  label: z.string().max(300).nullable().optional(),
  min: finiteNumber.min(-1_000_000).max(1_000_000),
  max: finiteNumber.min(-1_000_000).max(1_000_000),
  value: finiteNumber.min(-1_000_000).max(1_000_000),
  step: finiteNumber.positive().max(1_000_000).nullable().optional(),
}).strict().refine((control) => control.min < control.max, 'Invalid control bounds')
  .refine((control) => control.value >= control.min && control.value <= control.max, 'Control value outside bounds')

const labDomainSchema = z.record(
  z.string().min(1).max(100),
  z.tuple([finiteNumber, finiteNumber])
).superRefine((domain, context) => {
  if (Object.keys(domain).length > 8) {
    context.addIssue({ code: 'custom', message: 'Too many Lab domain variables' })
  }
  for (const [key, [minimum, maximum]] of Object.entries(domain)) {
    if (minimum >= maximum || Math.abs(minimum) > 1_000_000 || Math.abs(maximum) > 1_000_000) {
      context.addIssue({ code: 'custom', path: [key], message: 'Invalid Lab domain bounds' })
    }
  }
})

function validateBoundedLabValue(value: unknown, context: z.RefinementCtx, depth = 0): void {
  if (depth > 5) {
    context.addIssue({ code: 'custom', message: 'Lab object nesting is too deep' })
    return
  }
  if (typeof value === 'string') {
    if (value.length > 4000) context.addIssue({ code: 'custom', message: 'Lab object string is too long' })
    return
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || Math.abs(value) > 1_000_000) {
      context.addIssue({ code: 'custom', message: 'Lab object number is outside the safe range' })
    }
    return
  }
  if (value === null || typeof value === 'boolean') return
  if (Array.isArray(value)) {
    if (value.length > 64) context.addIssue({ code: 'custom', message: 'Lab object array is too large' })
    value.forEach((item) => validateBoundedLabValue(item, context, depth + 1))
    return
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value)
    if (entries.length > 32) context.addIssue({ code: 'custom', message: 'Lab object has too many fields' })
    for (const [key, item] of entries) {
      if (!key || key.length > 100) context.addIssue({ code: 'custom', message: 'Lab object key is invalid' })
      validateBoundedLabValue(item, context, depth + 1)
    }
    return
  }
  context.addIssue({ code: 'custom', message: 'Lab object contains an unsupported value' })
}

const labObjectsSchema = z.array(z.record(z.string(), z.unknown())).max(8).superRefine((objects, context) => {
  objects.forEach((object) => validateBoundedLabValue(object, context))
})

const labBase = {
  key: safeLabKeySchema,
  title: z.string().min(1).max(300),
  anchor_ids: z.array(z.string().min(1)).max(100),
  provenance: provenanceSchema,
  expressions: z.array(z.string().min(1).max(500)).max(8),
  domain: labDomainSchema,
  controls: z.array(labControlSchema).max(8),
  objects: labObjectsSchema,
}

export const labSpecSchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('function_plot'), ...labBase }).strict(),
  z.object({ kind: z.literal('parametric_curve'), ...labBase }).strict(),
  z.object({ kind: z.literal('vector_field'), ...labBase }).strict(),
  z.object({ kind: z.literal('geometry'), ...labBase }).strict(),
  z.object({ kind: z.literal('kinematics'), ...labBase }).strict(),
]).superRefine((spec, context) => {
  validateProvenance(spec, context)
  const count = spec.expressions.length
  if (spec.kind === 'function_plot' && count < 1) {
    context.addIssue({ code: 'custom', path: ['expressions'], message: 'A function plot requires at least one expression' })
  }
  if (['parametric_curve', 'vector_field', 'kinematics'].includes(spec.kind) && count !== 2) {
    context.addIssue({ code: 'custom', path: ['expressions'], message: 'This Lab requires exactly two expressions' })
  }
  if (spec.kind === 'geometry' && count !== 0) {
    context.addIssue({ code: 'custom', path: ['expressions'], message: 'Geometry Labs do not accept expressions' })
  }
  const keys = spec.controls.map((control) => control.key)
  if (new Set(keys).size !== keys.length) {
    context.addIssue({ code: 'custom', path: ['controls'], message: 'Lab control keys must be unique' })
  }
})
export type LabSpec = z.infer<typeof labSpecSchema>

export const chapterSectionSchema = z.object({
  key: z.string().min(1).max(100),
  title: z.string().min(1).max(300),
  markdown: z.string().min(1).max(100_000),
  anchor_ids: z.array(z.string().min(1)).max(200),
  provenance: provenanceSchema,
}).strict().superRefine(validateProvenance)

const formulaSchema = z.object({
  key: z.string().min(1).max(100),
  latex: z.string().min(1).max(4000),
  meaning: z.string().min(1).max(2000),
  anchor_ids: z.array(z.string().min(1)).max(100),
  unit_expression: z.string().max(500).nullable(),
  oracle_unit_expression: z.string().max(500).nullable(),
  provenance: provenanceSchema,
  oracle_expression: z.string().max(1000).nullable(),
  oracle_substitutions: z.record(z.string(), finiteNumber),
}).strict().superRefine(validateProvenance)
const workedExampleSchema = z.object({
  key: z.string().min(1).max(100),
  prompt: z.string().min(1).max(4000),
  steps: z.array(z.string()).min(1).max(50),
  answer: z.string().min(1).max(4000),
  anchor_ids: z.array(z.string().min(1)).max(100),
  oracle_expression: z.string().max(1000).nullable(),
  oracle_values: z.record(z.string(), finiteNumber), oracle_answer: finiteNumber.nullable(),
  unit_expression: z.string().max(500).nullable(),
  oracle_unit_expression: z.string().max(500).nullable(),
  provenance: provenanceSchema,
}).strict().superRefine(validateProvenance)
const exerciseSchema = z.object({
  key: z.string().min(1).max(100),
  prompt: z.string().min(1).max(4000),
  difficulty: z.enum(['core', 'challenge']),
  hints: z.array(z.string()).max(5),
  answer: z.string().min(1).max(4000),
  transfer_task: z.string().min(1).max(4000),
  anchor_ids: z.array(z.string().min(1)).max(100),
  oracle_expression: z.string().max(1000).nullable(),
  oracle_values: z.record(z.string(), finiteNumber), oracle_answer: finiteNumber.nullable(),
  provenance: provenanceSchema,
}).strict().superRefine(validateProvenance)

const chapterTextAttributionSchema = z.object({
  anchor_ids: z.array(z.string().min(1)).max(100),
  provenance: provenanceSchema,
}).strict().superRefine(validateProvenance)

const chapterTextAttributionsSchema = z.object({
  purpose: chapterTextAttributionSchema,
  prerequisites: z.array(chapterTextAttributionSchema).max(100),
  objectives: z.array(chapterTextAttributionSchema).max(100),
  definitions: z.array(chapterTextAttributionSchema).max(100),
  misconceptions: z.array(chapterTextAttributionSchema).max(100),
  pitfalls: z.array(chapterTextAttributionSchema).max(100),
  quick_reference: z.array(chapterTextAttributionSchema).max(100),
}).strict()

const physicsCheckBase = {
  key: z.string().min(1).max(100),
  anchor_ids: z.array(z.string().min(1)).min(1).max(100),
}

const safePhysicsExpression = z.string().min(1).max(1000).refine(
  (value) => !value.includes('__') && /^[A-Za-z0-9_+\-*/^()., \t]+$/.test(value),
  'Physics limit expression is unsafe',
)

export const physicsCheckSchema = z.discriminatedUnion('kind', [
  z.object({
    kind: z.literal('vector'),
    ...physicsCheckBase,
    actual_components: z.array(finiteNumber).min(2).max(3),
    expected_components: z.array(finiteNumber).min(2).max(3),
    absolute_tolerance: finiteNumber.min(0).max(1),
    relative_tolerance: finiteNumber.min(0).max(1),
  }).strict(),
  z.object({
    kind: z.literal('direction'),
    ...physicsCheckBase,
    actual: z.union([z.literal(-1), z.literal(0), z.literal(1)]),
    expected: z.union([z.literal(-1), z.literal(0), z.literal(1)]),
  }).strict(),
  z.object({
    kind: z.literal('reference_frame'),
    ...physicsCheckBase,
    actual: z.string().trim().min(1).max(200),
    expected: z.string().trim().min(1).max(200),
  }).strict(),
  z.object({
    kind: z.literal('boundary'),
    ...physicsCheckBase,
    value: finiteNumber,
    minimum: finiteNumber,
    maximum: finiteNumber,
  }).strict(),
  z.object({
    kind: z.literal('limit'),
    ...physicsCheckBase,
    expression: safePhysicsExpression,
    variable: z.string().regex(/^[A-Za-z]$/),
    point: finiteNumber,
    expected: finiteNumber,
    side: z.enum(['left', 'right', 'both']),
  }).strict(),
]).superRefine((check, context) => {
  if (
    check.kind === 'vector'
    && check.actual_components.length !== check.expected_components.length
  ) {
    context.addIssue({
      code: 'custom',
      path: ['expected_components'],
      message: 'Physics vectors must have the same dimension',
    })
  }
  if (check.kind === 'boundary' && check.minimum > check.maximum) {
    context.addIssue({
      code: 'custom',
      path: ['maximum'],
      message: 'Physics boundary interval is invalid',
    })
  }
})

export const chapterArtifactSchema = z.object({
  chapter_key: z.string().min(1).max(100),
  purpose: z.string().min(1).max(4000),
  prerequisites: z.array(z.string()).max(100),
  objectives: z.array(z.string()).min(1).max(100),
  sections: z.array(chapterSectionSchema).min(1).max(100),
  definitions: z.array(z.string()).max(100),
  formulas: z.array(formulaSchema).max(100),
  worked_examples: z.array(workedExampleSchema).max(100),
  labs: z.array(labSpecSchema).max(20),
  misconceptions: z.array(z.string()).max(100),
  pitfalls: z.array(z.string()).max(100),
  exercises: z.array(exerciseSchema).max(200),
  quick_reference: z.array(z.string()).max(100),
  citations: z.array(z.string()).max(500),
  attributions: chapterTextAttributionsSchema,
  physics_checks: z.array(physicsCheckSchema).max(100),
}).strict().superRefine((artifact, context) => {
  const parallelFields = [
    'prerequisites',
    'objectives',
    'definitions',
    'misconceptions',
    'pitfalls',
    'quick_reference',
  ] as const
  for (const field of parallelFields) {
    if (artifact[field].length !== artifact.attributions[field].length) {
      context.addIssue({
        code: 'custom',
        path: ['attributions', field],
        message: `${field} attributions must match rendered values exactly`,
      })
    }
  }
})
export type ChapterArtifact = z.infer<typeof chapterArtifactSchema>

export const chapterSchema = z.object({
  id: recordId,
  course_version: recordId,
  chapter_no: z.number().int().positive(),
  title: z.string().min(1),
  chapter_key: z.string().min(1),
  version_no: z.number().int().positive(),
  artifact: chapterArtifactSchema.nullable(),
  input_hash: z.string().nullable(),
  status: z.string(),
  published_at: timestamp,
  content: z.string().nullable(),
  review_status: z.string(),
  validation_status: z.string(),
  citations: z.array(z.record(z.string(), z.unknown())).nullable(),
  ...courseRecordDates,
}).strict()
export type Chapter = z.infer<typeof chapterSchema>

export const validationFindingSchema = z.object({
  kind: z.enum(['citation', 'formula', 'unit', 'numeric', 'physics', 'lab', 'review']),
  severity: z.enum(['info', 'warning', 'high', 'error']),
  item_key: z.string().min(1),
  anchor_ids: z.array(z.string()),
  status: z.enum(['open', 'uncertain', 'resolved', 'manual_check', 'acknowledged']),
  message: z.string().min(1),
  reviewer_run_id: z.string().nullable(),
  resolution_reason: z.string().nullable(),
}).strict()

export const courseFindingSchema = z.object({
  id: recordId,
  course: recordId,
  course_version: recordId.nullable(),
  chapter: recordId.nullable(),
  generation_run: recordId.nullable(),
  chapter_key: z.string().nullable(),
  finding: validationFindingSchema,
  severity: z.string(),
  status: z.string(),
  resolution_reason: z.string().nullable(),
  ...courseRecordDates,
}).strict()
export type CourseFinding = z.infer<typeof courseFindingSchema>

export const progressSchema = z.object({
  id: recordId,
  course: recordId,
  chapter: recordId.nullable(),
  chapter_key: z.string().nullable(),
  block_key: z.string().nullable(),
  orphan_status: z.string(),
  status: z.string(),
  ...courseRecordDates,
}).strict()
export type CourseProgress = z.infer<typeof progressSchema>

export const courseNoteSchema = z.object({
  id: recordId,
  course: recordId,
  chapter: recordId.nullable(),
  chapter_key: z.string().nullable(),
  block_key: z.string().nullable(),
  orphan_status: z.string(),
  content: z.string(),
  ...courseRecordDates,
}).strict()
export type CourseNote = z.infer<typeof courseNoteSchema>

export const courseLabSchema = z.object({
  id: recordId,
  lab_key: z.string().min(1),
  lab_type: z.enum(['function_plot', 'parametric_curve', 'vector_field', 'geometry', 'kinematics']),
  spec: labSpecSchema,
}).strict()
export type CourseLab = z.infer<typeof courseLabSchema>

export const courseAttemptSchema = z.object({
  id: recordId,
  lab: recordId,
  answers: z.record(z.string(), z.unknown()),
  status: z.string(),
  result: z.record(z.string(), z.unknown()).nullable(),
  course: recordId.nullable(),
  course_version: recordId.nullable(),
  chapter: recordId.nullable(),
  chapter_key: z.string().nullable(),
  exercise_key: z.string().nullable(),
  answer: z.string().nullable(),
  hints_used: z.number().int().nonnegative().nullable(),
  answer_revealed: z.boolean().nullable(),
  transfer_completed: z.boolean().nullable(),
  orphan_status: z.string().nullable(),
  ...courseRecordDates,
}).strict()
export type CourseAttempt = z.infer<typeof courseAttemptSchema>

export const courseAttemptWithLabSchema = z.object({
  lab_key: z.string().min(1),
  attempt: courseAttemptSchema,
}).strict()
export type CourseAttemptWithLab = z.infer<typeof courseAttemptWithLabSchema>

export const eligibleCourseSourceSchema = z.object({
  source_id: recordId,
  title: z.string().nullable().optional(),
  filename: z.string().min(1),
  kind: z.enum(['pdf', 'pptx']),
  role: sourceRoleSchema.nullable(),
  associated: z.boolean(),
}).strict().superRefine((source, context) => {
  const filename = source.filename.toLowerCase()
  const expected = source.kind === 'pdf' ? '.pdf' : '.pptx'
  if (!filename.endsWith(expected)) {
    context.addIssue({
      code: 'custom',
      path: ['filename'],
      message: `Expected a ${expected} file`,
    })
  }
})
export type EligibleCourseSource = z.infer<typeof eligibleCourseSourceSchema>

export const courseModelOptionSchema = z.object({
  adapter: z.enum(['codex_cli', 'open_notebook', 'ollama']),
  model: z.string().min(1).nullable(),
  reasoning_effort: reasoningEffortSchema.nullable(),
  reasoning_efforts: z.array(reasoningEffortSchema).optional(),
  optional: z.boolean(),
  configured: z.boolean(),
  selectable: z.boolean().optional(),
  name: z.string().optional(),
  provider: z.string().optional(),
  display_name: z.string().optional(),
}).strict().superRefine(validateOpenNotebookModelId)
export type CourseModelOption = z.infer<typeof courseModelOptionSchema>

export function isSelectableModel(option: CourseModelOption): option is CourseModelOption & { model: string } {
  return option.configured && option.model !== null && option.selectable !== false
}

export const courseModelOptionsSchema = z.object({
  defaults: z.record(z.string(), modelSelectionSchema),
  options: z.array(courseModelOptionSchema),
}).strict()
export type CourseModelOptions = z.infer<typeof courseModelOptionsSchema>

export const courseJobSchema = z.object({
  command_id: recordId,
  run_id: recordId,
  status: z.string().min(1),
}).strict()
export type CourseJob = z.infer<typeof courseJobSchema>

export interface CreateCourseRequest {
  title: string
  subject?: string | null
  description?: string | null
  language: string
  notebook_id?: string
}

export interface BuildEvidenceRequest {
  source_id: string
  role: SourceRole
  force: boolean
}

export interface GenerateOutlineRequest {
  anchor_ids: string[]
  prompt_version: string
  model: ModelSelection
  force: boolean
}

export interface GenerateChapterRequest {
  anchor_ids: string[]
  prompt_version: string
  model: ModelSelection
  force: boolean
}

export interface ReviewChapterRequest extends GenerateChapterRequest {
  escalation_model: ModelSelection
}

export interface CreateCourseAttemptRequest {
  answers: Record<string, unknown>
  exercise_key?: string
  answer?: string
  hints_used?: number
  answer_revealed?: boolean
  transfer_completed?: boolean
}

export const stableCourseKeySchema = z.string().regex(/^[a-z0-9][a-z0-9_-]{0,99}$/)
export const masteryStatusSchema = z.enum([
  'not_started',
  'learning',
  'practiced',
  'mastered',
  'review_due',
])
export type MasteryStatus = z.infer<typeof masteryStatusSchema>

export const answerTypeSchema = z.enum([
  'numeric',
  'symbolic',
  'unit',
  'vector',
  'set',
  'multipart',
  'proof',
  'explanation',
])
export type AnswerType = z.infer<typeof answerTypeSchema>

export interface CourseAnswerFormat {
  kind: AnswerType
  component_count: number | null
  unit_required: boolean
  parts: CourseAnswerFormat[]
}

export const courseAnswerFormatSchema: z.ZodType<CourseAnswerFormat> = z.lazy(() => z.object({
  kind: answerTypeSchema,
  component_count: z.number().int().min(1).max(20).nullable(),
  unit_required: z.boolean(),
  parts: z.array(courseAnswerFormatSchema).max(20),
}).strict().superRefine((value, context) => {
  if ((value.kind === 'vector') !== (value.component_count !== null)) {
    context.addIssue({
      code: 'custom',
      path: ['component_count'],
      message: 'Only vector answers declare a component count',
    })
  }
  if ((value.kind === 'multipart') !== (value.parts.length > 0)) {
    context.addIssue({
      code: 'custom',
      path: ['parts'],
      message: 'Only multipart answers declare parts',
    })
  }
  if (value.kind === 'unit' && !value.unit_required) {
    context.addIssue({
      code: 'custom',
      path: ['unit_required'],
      message: 'Unit answers require a unit',
    })
  }
  if (!['unit', 'vector'].includes(value.kind) && value.unit_required) {
    context.addIssue({
      code: 'custom',
      path: ['unit_required'],
      message: 'This answer format does not accept a unit',
    })
  }
}))

export const transferDimensionSchema = z.enum([
  'representation',
  'inverse_or_constructive',
  'constraints_frame_or_regime',
  'method_comparison',
  'proof_counterexample_generalization',
  'math_physics_context',
])

export const difficultyVectorSchema = z.object({
  concept_count: z.number().int().min(0).max(20),
  reasoning_steps: z.number().int().min(0).max(20),
  symbolic_depth: z.number().int().min(0).max(20),
  representation_shifts: z.number().int().min(0).max(20),
  proof_burden: z.number().int().min(0).max(20),
  physics_constraints: z.number().int().min(0).max(20),
}).strict()
export type DifficultyVector = z.infer<typeof difficultyVectorSchema>

const positionPayloadSchema = z.object({
  block_key: stableCourseKeySchema.nullable(),
}).strict()
const hintViewedPayloadSchema = z.object({
  attempt_key: stableCourseKeySchema,
  hint_index: z.number().int().min(1).max(4),
}).strict()
const transferTaskPayloadSchema = z.object({
  attempt_key: stableCourseKeySchema,
  transfer_task_key: stableCourseKeySchema.nullable(),
}).strict()
const gradedPayloadSchema = z.object({
  answer_revealed: z.boolean(),
  hints_used: z.number().int().min(0).max(4),
  attempt_key: stableCourseKeySchema,
  response_parts: z.array(z.string().min(1).max(4000)).min(1).max(20),
}).strict()
const transferCompletedPayloadSchema = z.object({
  attempt_key: stableCourseKeySchema,
  source_attempt_key: stableCourseKeySchema,
  transfer_task_key: stableCourseKeySchema,
  response_parts: z.array(z.string().min(1).max(4000)).min(1).max(20),
}).strict()
const reviewCompletedPayloadSchema = z.object({
  attempt_key: stableCourseKeySchema,
  correct: z.boolean(),
  answer_revealed: z.boolean(),
  hints_used: z.number().int().min(0).max(4),
  response_parts: z.array(z.string().min(1).max(4000)).min(1).max(20),
}).strict()

export const learningEventKindSchema = z.enum([
  'chapter_opened',
  'hint_viewed',
  'answer_revealed',
  'graded_correct',
  'graded_incorrect',
  'transfer_required',
  'transfer_completed',
  'review_completed',
  'reading_position',
])

const learningEventPayloadSchema = z.union([
  positionPayloadSchema,
  hintViewedPayloadSchema,
  transferTaskPayloadSchema,
  gradedPayloadSchema,
  transferCompletedPayloadSchema,
  reviewCompletedPayloadSchema,
])

function payloadMatchesEventKind(
  kind: z.infer<typeof learningEventKindSchema>,
  payload: z.infer<typeof learningEventPayloadSchema>,
) {
  switch (kind) {
    case 'chapter_opened':
    case 'reading_position':
      return positionPayloadSchema.safeParse(payload).success
    case 'hint_viewed':
      return hintViewedPayloadSchema.safeParse(payload).success
    case 'answer_revealed':
    case 'transfer_required':
      return transferTaskPayloadSchema.safeParse(payload).success
    case 'graded_correct':
    case 'graded_incorrect':
      return gradedPayloadSchema.safeParse(payload).success
    case 'transfer_completed':
      return transferCompletedPayloadSchema.safeParse(payload).success
    case 'review_completed':
      return reviewCompletedPayloadSchema.safeParse(payload).success
  }
}

export const learningEventSchema = z.object({
  event_id: stableCourseKeySchema,
  course_id: recordId,
  course_version_id: recordId,
  chapter_key: stableCourseKeySchema,
  concept_key: stableCourseKeySchema.nullable(),
  exercise_key: stableCourseKeySchema.nullable(),
  kind: learningEventKindSchema,
  payload: learningEventPayloadSchema,
  occurred_at: z.string().datetime({ offset: true }),
}).strict().superRefine((value, context) => {
  if (!payloadMatchesEventKind(value.kind, value.payload)) {
    context.addIssue({
      code: 'custom',
      path: ['payload'],
      message: 'Payload does not match the learning event kind',
    })
  }
  const isActivity = value.kind === 'chapter_opened' || value.kind === 'reading_position'
  const exerciseEvent = !isActivity
  const conceptEvent = exerciseEvent && value.kind !== 'hint_viewed'
  if (isActivity && (value.concept_key !== null || value.exercise_key !== null)) {
    context.addIssue({
      code: 'custom',
      path: ['kind'],
      message: 'Activity events cannot claim a concept or exercise',
    })
  }
  if (exerciseEvent && value.exercise_key === null) {
    context.addIssue({
      code: 'custom',
      path: ['exercise_key'],
      message: 'Exercise events require an exercise key',
    })
  }
  if (conceptEvent && value.concept_key === null) {
    context.addIssue({
      code: 'custom',
      path: ['concept_key'],
      message: 'Concept events require a concept key',
    })
  }
  if (
    value.kind === 'reading_position'
    && 'block_key' in value.payload
    && value.payload.block_key === null
  ) {
    context.addIssue({
      code: 'custom',
      path: ['payload', 'block_key'],
      message: 'Reading positions require a block key',
    })
  }
})
export type LearningEvent = z.infer<typeof learningEventSchema>

export const conceptMasterySchema = z.object({
  course_id: recordId,
  course_version_id: recordId,
  chapter_key: stableCourseKeySchema,
  concept_key: stableCourseKeySchema,
  status: masteryStatusSchema,
  successful_exercise_keys: z.array(stableCourseKeySchema).max(200),
  unrevealed_success_count: z.number().int().min(0).max(200),
  review_level: z.number().int().min(0).max(5),
  review_due_at: z.string().datetime({ offset: true }).nullable(),
  last_event_at: z.string().datetime({ offset: true }).nullable(),
  snapshot_hash: z.string().regex(/^[0-9a-f]{64}$/),
}).strict()
export type ConceptMastery = z.infer<typeof conceptMasterySchema>

export const reviewQueueItemSchema = z.object({
  chapter_key: stableCourseKeySchema,
  concept_key: stableCourseKeySchema,
  status: z.literal('review_due'),
  due_at: z.string().datetime({ offset: true }),
  interval_days: z.union([
    z.literal(1), z.literal(3), z.literal(7), z.literal(14), z.literal(30),
  ]),
}).strict()
export type ReviewQueueItem = z.infer<typeof reviewQueueItemSchema>

export const courseLearningChapterOverviewSchema = z.object({
  chapter_key: stableCourseKeySchema,
  chapter_no: z.number().int().positive(),
  title: z.string().min(1),
  snapshot_token: sha256Schema,
  latest_position: learningEventSchema.nullable(),
}).strict()

export const courseConceptSchema = z.object({
  key: stableCourseKeySchema,
  label: z.string().min(1).max(300),
}).strict()

export const courseLearningOverviewSchema = z.object({
  course_id: courseRecordId,
  course_version_id: courseVersionRecordId,
  chapters: z.array(courseLearningChapterOverviewSchema),
  concepts: z.array(courseConceptSchema),
  masteries: z.array(conceptMasterySchema),
  review_queue: z.array(reviewQueueItemSchema),
}).strict()
export type CourseLearningOverview = z.infer<typeof courseLearningOverviewSchema>

export const courseTransferTaskSchema = z.object({
  key: stableCourseKeySchema,
  prompt: z.string().min(1).max(12_000),
  invariant_concept_keys: z.array(stableCourseKeySchema).min(1).max(50),
  dimensions: z.array(transferDimensionSchema).min(1).max(6),
  answer_type: answerTypeSchema,
  answer_format: courseAnswerFormatSchema,
  difficulty: difficultyVectorSchema,
  anchor_ids: z.array(z.string().min(1)).max(100),
}).strict()
export type CourseTransferTask = z.infer<typeof courseTransferTaskSchema>

export const courseExerciseSchema = z.object({
  key: stableCourseKeySchema,
  chapter_key: stableCourseKeySchema,
  prompt: z.string().min(1).max(12_000),
  concept_keys: z.array(stableCourseKeySchema).min(1).max(50),
  exercise_type: z.enum([
    'worked_source',
    'source_practice',
    'generated_core',
    'generated_challenge',
    'transfer',
  ]),
  answer_type: answerTypeSchema,
  answer_format: courseAnswerFormatSchema,
  snapshot_token: sha256Schema,
  source_anchor_ids: z.array(z.string().min(1)).max(100),
  source_number: z.string().min(1).max(100).nullable(),
  source_section: z.string().min(1).max(300).nullable(),
  difficulty: difficultyVectorSchema,
  is_core: z.boolean(),
  is_gating: z.boolean(),
  is_source_level: z.boolean(),
  transfer: courseTransferTaskSchema.nullable(),
}).strict()
export type CourseExercise = z.infer<typeof courseExerciseSchema>

export interface GradeResult {
  correct: boolean | null
  advisory: boolean
  grants_mastery: boolean
  feedback_code: 'correct' | 'incorrect' | 'invalid_answer' | 'advisory'
  part_results: GradeResult[]
}

export const gradeResultSchema: z.ZodType<GradeResult> = z.lazy(() => z.object({
  correct: z.boolean().nullable(),
  advisory: z.boolean(),
  grants_mastery: z.boolean(),
  feedback_code: z.enum(['correct', 'incorrect', 'invalid_answer', 'advisory']),
  part_results: z.array(gradeResultSchema).max(20),
}).strict().superRefine((value, context) => {
  if (value.advisory) {
    if (
      value.correct !== null
      || value.grants_mastery
      || value.feedback_code !== 'advisory'
      || value.part_results.length > 0
    ) {
      context.addIssue({ code: 'custom', message: 'Advisory result fields are inconsistent' })
    }
    return
  }
  if (value.correct === null || value.grants_mastery !== value.correct) {
    context.addIssue({ code: 'custom', message: 'Objective result fields are inconsistent' })
  }
  if (value.feedback_code === 'advisory') {
    context.addIssue({ code: 'custom', path: ['feedback_code'], message: 'Objective results cannot be advisory' })
  }
  if (value.correct === true && value.feedback_code !== 'correct') {
    context.addIssue({ code: 'custom', path: ['feedback_code'], message: 'Feedback contradicts a correct result' })
  }
  if (
    value.correct === false
    && value.feedback_code !== 'incorrect'
    && value.feedback_code !== 'invalid_answer'
  ) {
    context.addIssue({ code: 'custom', path: ['feedback_code'], message: 'Feedback contradicts an incorrect result' })
  }
  if (value.part_results.length > 0) {
    const partsCorrect = value.part_results.every((part) => part.correct === true)
    const partsInvalid = value.part_results.some((part) => part.feedback_code === 'invalid_answer')
    const expectedFeedback = partsCorrect ? 'correct' : partsInvalid ? 'invalid_answer' : 'incorrect'
    if (value.part_results.some((part) => part.advisory)) {
      context.addIssue({ code: 'custom', path: ['part_results'], message: 'Objective parts cannot be advisory' })
    }
    if (value.correct !== partsCorrect || value.feedback_code !== expectedFeedback) {
      context.addIssue({ code: 'custom', path: ['part_results'], message: 'Multipart result contradicts its parts' })
    }
  }
}))

export const courseExerciseGradeResponseSchema = z.object({
  grade: gradeResultSchema,
  mastery: conceptMasterySchema.nullable(),
  event_key: stableCourseKeySchema.nullable(),
  snapshot_token: sha256Schema,
}).strict()
export type CourseExerciseGradeResponse = z.infer<typeof courseExerciseGradeResponseSchema>

export const courseExerciseHintResponseSchema = z.object({
  snapshot_token: sha256Schema,
  hint_index: z.number().int().min(1).max(4),
  total_hints: z.number().int().min(1).max(4),
  hint: z.string().min(1).max(2000),
  event: learningEventSchema,
  mastery: conceptMasterySchema.nullable(),
}).strict()
export type CourseExerciseHintResponse = z.infer<typeof courseExerciseHintResponseSchema>

export const courseExerciseRevealResponseSchema = z.object({
  snapshot_token: sha256Schema,
  answer: z.unknown(),
  transfer: courseTransferTaskSchema.nullable(),
  events: z.array(learningEventSchema).min(1).max(2),
  mastery: conceptMasterySchema.nullable(),
}).strict()
export type CourseExerciseRevealResponse = z.infer<typeof courseExerciseRevealResponseSchema>

export const courseLearningEventResponseSchema = z.object({
  event: learningEventSchema,
  mastery: conceptMasterySchema.nullable(),
}).strict()

const activityEventBase = {
  snapshot_token: sha256Schema,
  idempotency_key: stableCourseKeySchema,
  chapter_key: stableCourseKeySchema,
}

export const courseLearningEventRequestSchema = z.discriminatedUnion('kind', [
  z.object({
    ...activityEventBase,
    kind: z.literal('chapter_opened'),
    payload: positionPayloadSchema,
  }).strict(),
  z.object({
    ...activityEventBase,
    kind: z.literal('reading_position'),
    payload: z.object({ block_key: stableCourseKeySchema }).strict(),
  }).strict(),
])
export type CourseLearningEventRequest = z.input<typeof courseLearningEventRequestSchema>

const boundedJsonAnswerSchema = z.unknown().superRefine((value, context) => {
  try {
    const encoded = JSON.stringify(value)
    if (encoded === undefined || new TextEncoder().encode(encoded).length > 32_000) {
      context.addIssue({ code: 'custom', message: 'Answer must be bounded JSON' })
    }
  } catch {
    context.addIssue({ code: 'custom', message: 'Answer must be valid JSON' })
  }
})

const exerciseActionBase = {
  snapshot_token: sha256Schema,
  chapter_key: stableCourseKeySchema,
  concept_key: stableCourseKeySchema,
  attempt_key: stableCourseKeySchema,
}

export const courseExerciseGradeRequestSchema = z.object({
  ...exerciseActionBase,
  answer: boundedJsonAnswerSchema,
  hints_used: z.number().int().min(0).max(4),
  answer_revealed: z.boolean(),
  mode: z.enum(['practice', 'review']),
}).strict()
export type CourseExerciseGradeRequest = z.input<typeof courseExerciseGradeRequestSchema>

export const courseExerciseHintRequestSchema = z.object({
  ...exerciseActionBase,
  idempotency_key: stableCourseKeySchema,
  hint_index: z.number().int().min(1).max(4),
}).strict()
export type CourseExerciseHintRequest = z.input<typeof courseExerciseHintRequestSchema>

export const courseExerciseRevealRequestSchema = z.object({
  ...exerciseActionBase,
  idempotency_key: stableCourseKeySchema,
}).strict()
export type CourseExerciseRevealRequest = z.input<typeof courseExerciseRevealRequestSchema>

export const courseTransferGradeRequestSchema = z.object({
  ...exerciseActionBase,
  source_attempt_key: stableCourseKeySchema,
  transfer_task_key: stableCourseKeySchema,
  answer: boundedJsonAnswerSchema,
}).strict()
export type CourseTransferGradeRequest = z.input<typeof courseTransferGradeRequestSchema>

const learnerChapterSectionSchema = z.object({
  block_key: stableCourseKeySchema,
  title: z.string().min(1).max(300),
  markdown: z.string().min(1).max(100_000),
  anchor_ids: z.array(z.string().min(1)).max(200),
  provenance: provenanceSchema,
}).strict()

const learnerFormulaSchema = z.object({
  key: stableCourseKeySchema,
  latex: z.string().min(1).max(4000),
  meaning: z.string().min(1).max(2000),
  anchor_ids: z.array(z.string().min(1)).max(100),
  unit_expression: z.string().max(500).nullable(),
  provenance: provenanceSchema,
}).strict()

const learnerWorkedExampleSchema = z.object({
  key: stableCourseKeySchema,
  prompt: z.string().min(1).max(4000),
  steps: z.array(z.string().min(1)).min(1).max(50),
  answer: z.string().min(1).max(4000),
  anchor_ids: z.array(z.string().min(1)).max(100),
  unit_expression: z.string().max(500).nullable(),
  provenance: provenanceSchema,
}).strict()

export const courseLearnerChapterArtifactSchema = z.object({
  purpose: z.string().min(1).max(4000),
  prerequisites: z.array(z.string()).max(100),
  objectives: z.array(z.string().min(1)).min(1).max(100),
  sections: z.array(learnerChapterSectionSchema).min(1).max(100),
  definitions: z.array(z.string()).max(100),
  formulas: z.array(learnerFormulaSchema).max(100),
  worked_examples: z.array(learnerWorkedExampleSchema).max(100),
  misconceptions: z.array(z.string()).max(100),
  pitfalls: z.array(z.string()).max(100),
  quick_reference: z.array(z.string()).max(100),
  citations: z.array(z.string().min(1)).max(500),
}).strict()
export type CourseLearnerChapterArtifact = z.infer<typeof courseLearnerChapterArtifactSchema>

export const courseLearnerChapterResponseSchema = z.object({
  course_id: courseRecordId,
  course_version_id: courseVersionRecordId,
  chapter_key: stableCourseKeySchema,
  chapter_no: z.number().int().positive(),
  title: z.string().min(1),
  status: z.literal('published'),
  snapshot_token: sha256Schema,
  artifact: courseLearnerChapterArtifactSchema,
}).strict()
export type CourseLearnerChapterResponse = z.infer<typeof courseLearnerChapterResponseSchema>

const normalizedBboxSchema = z.tuple([
  z.number().min(0).max(1),
  z.number().min(0).max(1),
  z.number().min(0).max(1),
  z.number().min(0).max(1),
])

export const courseLearnerSourceSchema = z.object({
  anchor_id: z.string().min(1).max(300),
  filename: z.string().min(1).max(500),
  kind: z.enum(['pdf_page', 'pptx_slide']),
  index: z.number().int().positive(),
  quote: z.string().min(1).max(4000),
  source_role: sourceRoleSchema,
  bbox: normalizedBboxSchema.nullable(),
}).strict()
export type CourseLearnerSource = z.infer<typeof courseLearnerSourceSchema>

export const courseLearnerSourcesResponseSchema = z.object({
  snapshot_token: sha256Schema,
  sources: z.array(courseLearnerSourceSchema),
}).strict()
export type CourseLearnerSourcesResponse = z.infer<typeof courseLearnerSourcesResponseSchema>

export const courseLearnerNoteSchema = z.object({
  note_id: z.string().regex(/^course_note:[^:]+$/),
  block_key: stableCourseKeySchema,
  content: z.string().min(1).max(20_000),
  orphan_status: z.enum(['active', 'orphaned']),
  created: z.string().datetime({ offset: true }).nullable(),
}).strict()
export type CourseLearnerNote = z.infer<typeof courseLearnerNoteSchema>

export const courseLearnerNotesResponseSchema = z.object({
  snapshot_token: sha256Schema,
  notes: z.array(courseLearnerNoteSchema),
}).strict()
export type CourseLearnerNotesResponse = z.infer<typeof courseLearnerNotesResponseSchema>

export const courseLearnerNoteCreateRequestSchema = z.object({
  snapshot_token: sha256Schema,
  block_key: stableCourseKeySchema,
  content: z.string().min(1).max(20_000),
}).strict()
export type CourseLearnerNoteCreateRequest = z.input<typeof courseLearnerNoteCreateRequestSchema>

export const courseTutorTurnSchema = z.object({
  turn_no: z.number().int().positive(),
  role: z.enum(['user', 'assistant']),
  content: z.string().min(1).max(20_000),
  anchor_ids: z.array(z.string().min(1)).max(100),
  answer_revealed: z.boolean(),
}).strict()
export type CourseTutorTurn = z.infer<typeof courseTutorTurnSchema>

export const courseTutorResponseSchema = z.object({
  session_id: z.string().regex(/^course_tutor_session:[^:]+$/),
  turn: courseTutorTurnSchema,
  insufficient_evidence: z.boolean(),
}).strict()

export const courseTutorSessionCreateRequestSchema = z.object({
  snapshot_token: sha256Schema,
  chapter_key: stableCourseKeySchema,
  model: modelSelectionSchema,
}).strict()
export type CourseTutorSessionCreateRequest = z.input<typeof courseTutorSessionCreateRequestSchema>

export const courseTutorSessionSchema = z.object({
  session_id: z.string().regex(/^course_tutor_session:[^:]+$/),
  course_version_id: courseVersionRecordId,
  chapter_key: stableCourseKeySchema,
  model: modelSelectionSchema,
  status: z.enum(['active', 'closed', 'stale']),
  turns: z.array(courseTutorTurnSchema).max(2000),
  created: z.string().datetime({ offset: true }).nullable(),
}).strict()
export type CourseTutorSession = z.infer<typeof courseTutorSessionSchema>

export const courseTutorMessageRequestSchema = z.object({
  snapshot_token: sha256Schema,
  content: z.string().min(1).max(20_000),
  intent: z.enum(['explain', 'diagnose', 'hint', 'reveal']),
  exercise_key: stableCourseKeySchema.optional(),
  concept_key: stableCourseKeySchema.optional(),
  attempt_key: stableCourseKeySchema.optional(),
}).strict().superRefine((value, context) => {
  const revealValues = [value.exercise_key, value.concept_key, value.attempt_key]
  if (value.intent === 'reveal' && revealValues.some((item) => item === undefined)) {
    context.addIssue({
      code: 'custom',
      path: ['intent'],
      message: 'Reveal requires exercise, concept, and attempt keys',
    })
  }
  if (value.intent !== 'reveal' && revealValues.some((item) => item !== undefined)) {
    context.addIssue({
      code: 'custom',
      path: ['intent'],
      message: 'Exercise scope is accepted only for reveal',
    })
  }
})
export type CourseTutorMessageRequest = z.input<typeof courseTutorMessageRequestSchema>

export const courseTutorMessageResponseSchema = z.object({
  snapshot_token: sha256Schema,
  response: courseTutorResponseSchema,
}).strict()
export type CourseTutorMessageResponse = z.infer<typeof courseTutorMessageResponseSchema>

const numericGraderSpecSchema = z.object({
  kind: z.literal('numeric'),
  expected: z.string().min(1).max(500),
  absolute_tolerance: finiteNumber.nonnegative(),
  relative_tolerance: finiteNumber.nonnegative(),
}).strict()

const symbolicGraderSpecSchema = z.object({
  kind: z.literal('symbolic'),
  expected_expression: z.string().min(1).max(2000),
  allowed_symbols: z.array(stableCourseKeySchema).max(100),
}).strict()

const unitGraderSpecSchema = z.object({
  kind: z.literal('unit'),
  expected_value: z.string().min(1).max(500),
  expected_unit: z.string().min(1).max(200),
  absolute_tolerance: finiteNumber.nonnegative(),
  relative_tolerance: finiteNumber.nonnegative(),
}).strict()

const vectorGraderSpecSchema = z.object({
  kind: z.literal('vector'),
  expected_components: z.array(z.string().min(1).max(500)).min(1).max(4),
  expected_unit: z.string().min(1).max(200).nullable(),
  absolute_tolerance: finiteNumber.nonnegative(),
  relative_tolerance: finiteNumber.nonnegative(),
}).strict()

const setGraderSpecSchema = z.object({
  kind: z.literal('set'),
  expected_items: z.array(z.string().min(1).max(500)).max(200),
  order_matters: z.boolean(),
}).strict()

const objectiveGraderSpecSchema = z.discriminatedUnion('kind', [
  numericGraderSpecSchema,
  symbolicGraderSpecSchema,
  unitGraderSpecSchema,
  vectorGraderSpecSchema,
  setGraderSpecSchema,
])

const multipartGraderSpecSchema = z.object({
  kind: z.literal('multipart'),
  parts: z.array(objectiveGraderSpecSchema).min(2).max(20),
}).strict()

const advisoryGraderSpecSchema = z.object({
  kind: z.literal('advisory'),
  rubric: z.string().min(1).max(8000),
  grants_mastery: z.literal(false),
}).strict()

export const graderSpecSchema = z.discriminatedUnion('kind', [
  numericGraderSpecSchema,
  symbolicGraderSpecSchema,
  unitGraderSpecSchema,
  vectorGraderSpecSchema,
  setGraderSpecSchema,
  multipartGraderSpecSchema,
  advisoryGraderSpecSchema,
])

const transferDimensionEvidenceSchema = z.object({
  dimension: transferDimensionSchema,
  source_structure: z.string().min(1).max(2000),
  target_structure: z.string().min(1).max(2000),
  rationale: z.string().min(1).max(4000),
}).strict()

export const transferTaskSpecSchema = z.object({
  key: stableCourseKeySchema,
  prompt: z.string().min(1).max(12_000),
  invariant_concept_keys: z.array(stableCourseKeySchema).min(1).max(50),
  dimensions: z.array(transferDimensionSchema).min(1).max(6),
  change_evidence: z.array(transferDimensionEvidenceSchema).max(6),
  answer_type: answerTypeSchema,
  difficulty: difficultyVectorSchema,
  grader: graderSpecSchema,
  anchor_ids: z.array(z.string().min(1)).max(100),
}).strict()
export type TransferTaskSpec = z.infer<typeof transferTaskSpecSchema>

export const exerciseBlueprintSchema = z.object({
  key: stableCourseKeySchema,
  chapter_key: stableCourseKeySchema,
  prompt: z.string().min(1).max(12_000),
  concept_keys: z.array(stableCourseKeySchema).min(1).max(50),
  exercise_type: z.enum([
    'worked_source', 'source_practice', 'generated_core',
    'generated_challenge', 'transfer',
  ]),
  answer_type: answerTypeSchema,
  hints: z.array(z.string().min(1).max(2000)).max(4),
  source_anchor_ids: z.array(z.string().min(1)).max(100),
  source_number: z.string().min(1).max(100).nullable(),
  source_section: z.string().min(1).max(300).nullable(),
  difficulty: difficultyVectorSchema,
  grader: graderSpecSchema,
  is_core: z.boolean(),
  is_gating: z.boolean(),
  is_source_level: z.boolean(),
  transfer_task: transferTaskSpecSchema.nullable(),
}).strict().superRefine((value, context) => {
  if (value.is_core !== value.is_gating) {
    context.addIssue({ code: 'custom', path: ['is_gating'], message: 'Core and gating flags must match' })
  }
  if ((value.is_core || value.is_source_level) && value.source_anchor_ids.length === 0) {
    context.addIssue({ code: 'custom', path: ['source_anchor_ids'], message: 'Source-level exercise needs evidence' })
  }
})
export type ExerciseBlueprint = z.infer<typeof exerciseBlueprintSchema>

export const draftOperationSchema = z.discriminatedUnion('kind', [
  z.object({
    kind: z.literal('replace_text'), block_key: stableCourseKeySchema,
    text: z.string().min(1).max(20_000), anchor_ids: z.array(z.string().min(1)).max(100),
  }).strict(),
  z.object({
    kind: z.literal('replace_formula'), block_key: stableCourseKeySchema,
    latex: z.string().min(1).max(4000), anchor_ids: z.array(z.string().min(1)).max(100),
  }).strict(),
  z.object({
    kind: z.literal('replace_exercise'), block_key: stableCourseKeySchema,
    exercise: exerciseBlueprintSchema,
  }).strict(),
  z.object({
    kind: z.literal('replace_transfer'), block_key: stableCourseKeySchema,
    transfer_task: transferTaskSpecSchema,
  }).strict(),
  z.object({
    kind: z.literal('replace_lab'), block_key: stableCourseKeySchema,
    lab_spec: labSpecSchema,
  }).strict(),
])
export type DraftOperation = z.infer<typeof draftOperationSchema>

export const courseDraftOperationRequestSchema = z.object({
  revision_token: sha256Schema,
  operation: draftOperationSchema,
}).strict()
export type CourseDraftOperationRequest = z.input<typeof courseDraftOperationRequestSchema>

export const courseDraftResponseSchema = z.object({
  chapter_key: stableCourseKeySchema,
  chapter_status: z.string().min(1).max(50),
  editable: z.boolean(),
  revision_no: z.number().int().nonnegative(),
  revision_token: sha256Schema,
  revision_status: z.enum(['draft', 'validated']).nullable(),
  artifact_hash: sha256Schema,
  artifact: chapterArtifactSchema,
  exercises: z.array(exerciseBlueprintSchema).max(500),
}).strict()
export type CourseDraft = z.infer<typeof courseDraftResponseSchema>

export const courseDraftValidationResponseSchema = z.object({
  draft: courseDraftResponseSchema,
  valid: z.boolean(),
  checked: z.array(z.enum([
    'formula', 'unit', 'numeric', 'physics', 'citation', 'structure',
  ])).max(6),
  findings: z.array(validationFindingSchema).max(500),
}).strict()
export type CourseDraftValidationResponse = z.infer<typeof courseDraftValidationResponseSchema>

export const courseBundleManifestSchema = z.object({
  schema_version: z.literal(1),
  app_version: z.string().min(1).max(100),
  course_title: z.string().min(1).max(300),
  exported_at: z.string().datetime({ offset: true }),
  record_counts: z.array(z.object({
    record_type: stableCourseKeySchema,
    count: z.number().int().nonnegative(),
  }).strict()).max(100),
  files: z.array(z.object({
    path: z.string().min(1).max(500),
    size_bytes: z.number().int().nonnegative(),
    sha256: sha256Schema,
  }).strict()).max(10_000),
}).strict()
export type CourseBundleManifest = z.infer<typeof courseBundleManifestSchema>

export const courseExportResponseSchema = z.object({
  export_id: typedRecordId('course_export'),
  course_id: courseRecordId,
  status: z.enum(['queued', 'running', 'succeeded', 'failed', 'cancelled']),
  download_ready: z.boolean(),
  manifest: courseBundleManifestSchema.nullable(),
  error_message: z.string().nullable(),
}).strict()
export type CourseExportResponse = z.infer<typeof courseExportResponseSchema>

export const courseBundleImportResponseSchema = z.object({
  course_id: courseRecordId,
  course_title: z.string().min(1).max(300),
  record_counts: z.record(stableCourseKeySchema, z.number().int().nonnegative()),
}).strict()
export type CourseBundleImportResponse = z.infer<typeof courseBundleImportResponseSchema>
