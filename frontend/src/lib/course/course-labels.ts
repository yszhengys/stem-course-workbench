type Translate = (key: string) => string

const COURSE_STATUS_KEYS: Record<string, string> = {
  new: 'course.statusNew',
  queued: 'course.statusQueued',
  running: 'course.statusRunning',
  completed: 'course.statusCompleted',
  succeeded: 'course.statusSucceeded',
  failed: 'course.statusFailed',
  cancelled: 'course.statusCancelled',
  canceled: 'course.statusCancelled',
  draft: 'course.statusDraft',
  indexing: 'course.statusIndexing',
  outline_ready: 'course.statusOutlineReady',
  outline_approved: 'course.statusOutlineApproved',
  generating: 'course.statusGenerating',
  reviewing: 'course.statusReviewing',
  blocked: 'course.statusBlocked',
  ready: 'course.statusReady',
  published: 'course.statusPublished',
  pending: 'course.statusPending',
  processing: 'course.statusProcessing',
  not_started: 'course.statusNotStarted',
  in_progress: 'course.statusInProgress',
  submitted: 'course.statusSubmitted',
  checked: 'course.statusChecked',
  passed: 'course.statusPassed',
  attached: 'course.statusAttached',
  orphaned: 'course.statusOrphaned',
}

const FINDING_KIND_KEYS: Record<string, string> = {
  citation: 'course.findingCitation',
  formula: 'course.findingFormula',
  unit: 'course.findingUnit',
  numeric: 'course.findingNumeric',
  physics: 'course.findingPhysics',
  lab: 'course.findingLab',
  review: 'course.findingReview',
}

const FINDING_SEVERITY_KEYS: Record<string, string> = {
  info: 'course.severityInfo',
  warning: 'course.severityWarning',
  high: 'course.severityHigh',
  error: 'course.severityError',
}

const FINDING_STATUS_KEYS: Record<string, string> = {
  open: 'course.findingStatusOpen',
  uncertain: 'course.findingStatusUncertain',
  resolved: 'course.findingStatusResolved',
  manual_check: 'course.findingStatusManualCheck',
  acknowledged: 'course.findingStatusAcknowledged',
}

const LOCATOR_KIND_KEYS: Record<string, string> = {
  pdf_page: 'course.locatorPdfPage',
  pptx_slide: 'course.locatorPptxSlide',
}

const PROVENANCE_KEYS: Record<string, string> = {
  verbatim: 'course.provenanceVerbatim',
  adapted: 'course.provenanceAdapted',
  derived: 'course.provenanceDerived',
  pedagogical: 'course.provenancePedagogical',
  '补充': 'course.provenanceSupplement',
}

function translatedEnum(t: Translate, keys: Record<string, string>, value: string): string {
  return t(keys[value.toLowerCase()] ?? 'course.statusUnknown')
}

export const courseStatusLabel = (t: Translate, value: string) =>
  translatedEnum(t, COURSE_STATUS_KEYS, value)

export const findingKindLabel = (t: Translate, value: string) =>
  translatedEnum(t, FINDING_KIND_KEYS, value)

export const findingSeverityLabel = (t: Translate, value: string) =>
  translatedEnum(t, FINDING_SEVERITY_KEYS, value)

export const findingStatusLabel = (t: Translate, value: string) =>
  translatedEnum(t, FINDING_STATUS_KEYS, value)

export const locatorKindLabel = (t: Translate, value: string) =>
  translatedEnum(t, LOCATOR_KIND_KEYS, value)

export const provenanceLabel = (t: Translate, value: string) =>
  translatedEnum(t, PROVENANCE_KEYS, value)

export function sourceRoleLabel(t: Translate, value: string): string {
  return t(value === 'PRIMARY' ? 'course.primary' : value === 'SUPPLEMENT' ? 'course.supplement' : 'course.statusUnknown')
}
