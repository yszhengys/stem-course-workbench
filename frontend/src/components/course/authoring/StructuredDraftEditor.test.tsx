import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  useApplyCourseChapterDraftOperation,
  useCourseChapterDraft,
  useValidateCourseChapterDraft,
} from '@/lib/hooks/use-courses'
import type { CourseDraft } from '@/lib/types/course'
import { StructuredDraftEditor } from './StructuredDraftEditor'

vi.mock('@/lib/hooks/use-courses', () => ({
  useApplyCourseChapterDraftOperation: vi.fn(),
  useCourseChapterDraft: vi.fn(),
  useValidateCourseChapterDraft: vi.fn(),
}))
vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const save = vi.fn()
const validate = vi.fn()
const refetch = vi.fn()
const academicL1 = {
  level: 'L1' as const,
  method: 'self_consistency' as const,
  anchor_ids: [],
  reason: null,
  verified_at: null,
  artifact_hash: null,
}

const draft: CourseDraft = {
  chapter_key: 'limits',
  chapter_status: 'reviewing',
  editable: true,
  revision_no: 3,
  revision_token: 'a'.repeat(64),
  revision_status: 'draft',
  artifact_hash: 'b'.repeat(64),
  artifact: {
    chapter_key: 'limits',
    purpose: 'Understand limits.',
    prerequisites: [],
    objectives: ['Evaluate limits.'],
    sections: [{
      key: 'definition', title: 'Definition', markdown: 'Original definition.',
      anchor_ids: ['anchor:definition'], provenance: 'adapted',
    }],
    definitions: [],
    formulas: [{
      key: 'limit-law', latex: 'x+0=x', meaning: 'Identity',
      anchor_ids: ['anchor:formula'], unit_expression: null,
      oracle_unit_expression: null, provenance: 'adapted',
      oracle_expression: null, oracle_substitutions: {}, verification: academicL1,
    }],
    worked_examples: [],
    labs: [{
      kind: 'function_plot', key: 'limit-plot', title: 'Limit plot',
      anchor_ids: ['anchor:lab'], provenance: 'adapted', expressions: ['x'],
      domain: { x: [-2, 2] }, controls: [], objects: [],
    }],
    misconceptions: [], pitfalls: [], exercises: [], quick_reference: [], citations: [],
    attributions: {
      purpose: { anchor_ids: ['anchor:definition'], provenance: 'adapted' },
      prerequisites: [],
      objectives: [{ anchor_ids: ['anchor:definition'], provenance: 'adapted' }],
      definitions: [], misconceptions: [], pitfalls: [], quick_reference: [],
    },
    physics_checks: [],
  },
  exercises: [{
    key: 'limits-core', chapter_key: 'limits', prompt: 'Evaluate the limit.',
    concept_keys: ['limit-laws'], exercise_type: 'generated_core', answer_type: 'numeric',
    hints: ['Use the limit law.'], source_anchor_ids: ['anchor:exercise'],
    source_number: null, source_section: null,
    difficulty: {
      concept_count: 1, reasoning_steps: 2, symbolic_depth: 1,
      representation_shifts: 0, proof_burden: 0, physics_constraints: 0,
    },
    grader: {
      kind: 'numeric', expected: '2', absolute_tolerance: 0,
      relative_tolerance: 0,
    },
    is_core: true, is_gating: true, is_source_level: false,
    transfer_task: {
      key: 'limits-core-transfer', prompt: 'Read the graph.',
      invariant_concept_keys: ['limit-laws'], dimensions: ['representation'],
      change_evidence: [{
        dimension: 'representation', source_structure: 'symbolic',
        target_structure: 'graphical', rationale: 'Changes representation.',
      }],
      answer_type: 'numeric',
      difficulty: {
        concept_count: 1, reasoning_steps: 2, symbolic_depth: 0,
        representation_shifts: 1, proof_burden: 0, physics_constraints: 0,
      },
      grader: {
        kind: 'numeric', expected: '2', absolute_tolerance: 0,
        relative_tolerance: 0,
      },
      anchor_ids: ['anchor:exercise'],
    },
  }],
}

function queryResult(value: CourseDraft = draft) {
  return {
    data: value,
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch,
  }
}

describe('StructuredDraftEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    save.mockResolvedValue(draft)
    validate.mockResolvedValue({
      draft: { ...draft, revision_status: 'validated' },
      valid: false,
      checked: ['formula', 'unit', 'numeric'],
      findings: [{
        kind: 'formula', severity: 'error', item_key: 'limit-law', anchor_ids: [],
        status: 'manual_check', message: 'Formula requires a manual check.',
        reviewer_run_id: null, resolution_reason: null,
      }],
    })
    vi.mocked(useCourseChapterDraft).mockReturnValue(queryResult() as never)
    vi.mocked(useApplyCourseChapterDraftOperation).mockReturnValue({
      mutateAsync: save, isPending: false, error: null,
    } as never)
    vi.mocked(useValidateCourseChapterDraft).mockReturnValue({
      mutateAsync: validate, isPending: false, error: null,
    } as never)
  })

  it('saves a selected text block with its current evidence and revision token', async () => {
    render(<StructuredDraftEditor courseId="course:one" chapterKey="limits" />)

    fireEvent.change(screen.getByLabelText('course.draftBlock'), {
      target: { value: 'definition' },
    })
    fireEvent.change(screen.getByLabelText('course.draftText'), {
      target: { value: 'A reviewed definition.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.saveDraftChange' }))

    await waitFor(() => expect(save).toHaveBeenCalledWith({
      revision_token: 'a'.repeat(64),
      operation: {
        kind: 'replace_text', block_key: 'definition',
        text: 'A reviewed definition.', anchor_ids: ['anchor:definition'],
      },
    }))
  })

  it('uses typed formula controls and displays targeted validation results', async () => {
    render(<StructuredDraftEditor courseId="course:one" chapterKey="limits" />)

    fireEvent.change(screen.getByLabelText('course.draftKind'), {
      target: { value: 'formula' },
    })
    fireEvent.change(screen.getByLabelText('course.draftFormula'), {
      target: { value: 'x+1=1+x' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.saveDraftChange' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith({
      revision_token: 'a'.repeat(64),
      operation: {
        kind: 'replace_formula', block_key: 'limit-law', latex: 'x+1=1+x',
        anchor_ids: ['anchor:formula'],
      },
    }))

    fireEvent.click(screen.getByRole('button', { name: 'course.validateDraft' }))
    await waitFor(() => expect(validate).toHaveBeenCalledWith({
      revision_token: 'a'.repeat(64),
    }))
    expect(screen.getByText('Formula requires a manual check.')).toBeInTheDocument()
    expect(screen.getByText('formula · unit · numeric')).toBeInTheDocument()
  })

  it('updates exercises, transfer tasks and labs through bounded typed fields', async () => {
    render(<StructuredDraftEditor courseId="course:one" chapterKey="limits" />)

    fireEvent.change(screen.getByLabelText('course.draftKind'), {
      target: { value: 'exercise' },
    })
    fireEvent.change(screen.getByLabelText('course.draftExercisePrompt'), {
      target: { value: 'Evaluate this revised limit.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.saveDraftChange' }))
    await waitFor(() => expect(save).toHaveBeenLastCalledWith({
      revision_token: 'a'.repeat(64),
      operation: {
        kind: 'replace_exercise', block_key: 'limits-core',
        exercise: { ...draft.exercises[0], prompt: 'Evaluate this revised limit.' },
      },
    }))

    fireEvent.change(screen.getByLabelText('course.draftKind'), {
      target: { value: 'transfer' },
    })
    fireEvent.change(screen.getByLabelText('course.draftTransferPrompt'), {
      target: { value: 'Interpret the revised graph.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.saveDraftChange' }))
    await waitFor(() => expect(save).toHaveBeenLastCalledWith({
      revision_token: 'a'.repeat(64),
      operation: {
        kind: 'replace_transfer', block_key: 'limits-core-transfer',
        transfer_task: {
          ...draft.exercises[0].transfer_task,
          prompt: 'Interpret the revised graph.',
        },
      },
    }))

    fireEvent.change(screen.getByLabelText('course.draftKind'), {
      target: { value: 'lab' },
    })
    fireEvent.change(screen.getByLabelText('course.draftLabTitle'), {
      target: { value: 'Revised limit plot' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'course.saveDraftChange' }))
    await waitFor(() => expect(save).toHaveBeenLastCalledWith({
      revision_token: 'a'.repeat(64),
      operation: {
        kind: 'replace_lab', block_key: 'limit-plot',
        lab_spec: { ...draft.artifact.labs[0], title: 'Revised limit plot' },
      },
    }))
    expect(screen.queryByLabelText(/json/i)).not.toBeInTheDocument()
  })

  it('makes approved or published chapter artifacts read only', () => {
    vi.mocked(useCourseChapterDraft).mockReturnValue(queryResult({
      ...draft, chapter_status: 'published', editable: false,
    }) as never)
    render(<StructuredDraftEditor courseId="course:one" chapterKey="limits" />)

    expect(screen.getByText('course.draftReadOnly')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'course.saveDraftChange' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'course.validateDraft' })).toBeDisabled()
  })
})
