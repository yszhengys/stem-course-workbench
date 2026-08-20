import { describe, expect, it, vi } from 'vitest'

import {
  courseStatusLabel,
  exerciseDifficultyLabel,
  findingKindLabel,
  findingSeverityLabel,
  findingStatusLabel,
  locatorKindLabel,
  provenanceLabel,
  reasoningEffortLabel,
  subjectLabel,
} from './course-labels'

const t = vi.fn((key: string) => key)

describe('localized Course enum labels', () => {
  it('routes workflow and finding values through explicit translation keys', () => {
    expect(courseStatusLabel(t, 'outline_ready')).toBe('course.statusOutlineReady')
    expect(courseStatusLabel(t, 'not_started')).toBe('course.statusNotStarted')
    expect(findingKindLabel(t, 'formula')).toBe('course.findingFormula')
    expect(findingSeverityLabel(t, 'high')).toBe('course.severityHigh')
    expect(findingStatusLabel(t, 'manual_check')).toBe('course.findingStatusManualCheck')
    expect(locatorKindLabel(t, 'pptx_slide')).toBe('course.locatorPptxSlide')
    expect(provenanceLabel(t, 'pedagogical')).toBe('course.provenancePedagogical')
    expect(reasoningEffortLabel(t, 'xhigh')).toBe('course.effortXhigh')
    expect(subjectLabel(t, 'physics')).toBe('course.subjectPhysics')
    expect(exerciseDifficultyLabel(t, 'challenge')).toBe('course.difficultyChallenge')
  })

  it('uses a localized unknown label instead of exposing an untrusted enum value', () => {
    expect(courseStatusLabel(t, 'unexpected-backend-value')).toBe('course.statusUnknown')
  })
})
