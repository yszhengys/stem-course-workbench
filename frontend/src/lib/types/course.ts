import { z } from 'zod'

const recordId = z.string().min(3).refine((value) => value.includes(':'), {
  message: 'Expected a typed record ID',
})
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
