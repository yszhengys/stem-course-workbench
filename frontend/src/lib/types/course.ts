import { z } from 'zod'

const recordId = z.string().min(3).refine((value) => value.includes(':'), {
  message: 'Expected a typed record ID',
})
const timestamp = z.string().nullable().optional()
const finiteNumber = z.number().finite()

export const sourceRoleSchema = z.enum(['PRIMARY', 'SUPPLEMENT'])
export type SourceRole = z.infer<typeof sourceRoleSchema>

export const reasoningEffortSchema = z.enum(['low', 'medium', 'high', 'xhigh', 'max'])
export const modelSelectionSchema = z.object({
  adapter: z.enum(['codex_cli', 'open_notebook', 'ollama']),
  model: z.string().min(1),
  reasoning_effort: reasoningEffortSchema.nullable(),
}).strict()
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

export const outlineChapterSchema = z.object({
  key: z.string().min(1),
  title: z.string().min(1),
  purpose: z.string().min(1),
  prerequisite_keys: z.array(z.string()),
  objective_keys: z.array(z.string()).min(1),
  anchor_ids: z.array(z.string()).min(1),
  lab_keys: z.array(z.string()),
}).strict()

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

const labBase = {
  key: z.string().min(1).max(100),
  title: z.string().min(1).max(300),
  anchor_ids: z.array(z.string()).max(100),
  expressions: z.array(z.string().min(1).max(500)).max(8),
  domain: z.record(z.string(), z.tuple([finiteNumber, finiteNumber])),
  controls: z.array(labControlSchema).max(8),
  objects: z.array(z.record(z.string(), z.unknown())).max(8),
}

export const labSpecSchema = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('function_plot'), ...labBase }).strict(),
  z.object({ kind: z.literal('parametric_curve'), ...labBase }).strict(),
  z.object({ kind: z.literal('vector_field'), ...labBase }).strict(),
  z.object({ kind: z.literal('geometry'), ...labBase }).strict(),
  z.object({ kind: z.literal('kinematics'), ...labBase }).strict(),
])
export type LabSpec = z.infer<typeof labSpecSchema>

export const chapterSectionSchema = z.object({
  key: z.string().min(1),
  title: z.string().min(1),
  markdown: z.string().min(1),
  anchor_ids: z.array(z.string()).min(1),
  provenance: z.enum(['verbatim', 'adapted', 'derived', 'pedagogical', '补充']),
}).strict()

const formulaSchema = z.object({
  key: z.string(), latex: z.string(), meaning: z.string(), anchor_ids: z.array(z.string()),
  unit_expression: z.string().nullable(), oracle_unit_expression: z.string().nullable(),
  provenance: z.string(), oracle_expression: z.string().nullable(),
  oracle_substitutions: z.record(z.string(), finiteNumber),
}).strict()
const workedExampleSchema = z.object({
  key: z.string(), prompt: z.string(), steps: z.array(z.string()), answer: z.string(),
  anchor_ids: z.array(z.string()), oracle_expression: z.string().nullable(),
  oracle_values: z.record(z.string(), finiteNumber), oracle_answer: finiteNumber.nullable(),
  unit_expression: z.string().nullable(), oracle_unit_expression: z.string().nullable(),
  provenance: z.string(),
}).strict()
const exerciseSchema = z.object({
  key: z.string(), prompt: z.string(), difficulty: z.enum(['core', 'challenge']),
  hints: z.array(z.string()), answer: z.string(), transfer_task: z.string(),
  anchor_ids: z.array(z.string()), oracle_expression: z.string().nullable(),
  oracle_values: z.record(z.string(), finiteNumber), oracle_answer: finiteNumber.nullable(),
  provenance: z.string(),
}).strict()

export const chapterArtifactSchema = z.object({
  chapter_key: z.string().min(1),
  purpose: z.string().min(1),
  prerequisites: z.array(z.string()),
  objectives: z.array(z.string()).min(1),
  sections: z.array(chapterSectionSchema).min(1),
  definitions: z.array(z.string()),
  formulas: z.array(formulaSchema),
  worked_examples: z.array(workedExampleSchema),
  labs: z.array(labSpecSchema),
  misconceptions: z.array(z.string()),
  pitfalls: z.array(z.string()),
  exercises: z.array(exerciseSchema),
  quick_reference: z.array(z.string()),
  citations: z.array(z.string()),
}).strict()
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

export const eligibleCourseSourceSchema = z.object({
  source_id: recordId,
  title: z.string().nullable().optional(),
  filename: z.string().min(1),
  kind: z.enum(['pdf', 'pptx']),
  role: sourceRoleSchema.nullable(),
  associated: z.boolean(),
}).strict()
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
}).strict()
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
  available_lab_keys: string[]
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
